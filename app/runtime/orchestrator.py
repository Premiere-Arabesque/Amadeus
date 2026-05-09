from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from app.core.events import EventSource, EventType, RuntimeEvent
from app.core.outcomes import (
    ActionOutcome,
    ExecutionTraceEntry,
    OutcomeStatus,
    ReplanDecision,
    ReplanKind,
)
from app.core.state import DayPlanBlock, PlanOutlineStatus, PlanStep, PlanStepStatus, RuntimeState
from app.core.types import ExecutionGranularity, ExecutionMode, ExecutionZone
from app.runtime.clock import AdjustableClock, FunctionClock, RuntimeClock
from app.runtime.execution import ExecutionLoopContext
from app.runtime.interaction import InteractionExecutionResult, resolve_interaction_partner


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value)


@dataclass(slots=True)
class OrchestratorServices:
    planning: object
    execution: object
    replan: object
    interaction: object
    memory: object
    communication: object


class RuntimeOrchestrator:
    def __init__(
        self,
        *,
        services: OrchestratorServices,
        initial_state: RuntimeState | None = None,
        clock: RuntimeClock | None = None,
        now_provider: Callable[[], datetime] | None = None,
        interaction_cooldown_seconds: int = 180,
    ) -> None:
        self.services = services
        self.state = initial_state or RuntimeState()
        self.interaction_cooldown_seconds = max(0, interaction_cooldown_seconds)
        if clock is not None:
            self.clock = clock
        elif now_provider is not None:
            self.clock = FunctionClock(now_provider)
        else:
            self.clock = AdjustableClock()
        self._started_at = self.now()
        self._events: deque[RuntimeEvent] = deque()
        self._run_lock = asyncio.Lock()
        self._scheduler_task: asyncio.Task[None] | None = None
        self._scheduler_wakeup: asyncio.Event | None = None
        self._scheduler_paused = False

    async def start_scheduler(self) -> None:
        if self._scheduler_task is not None:
            return
        if self._scheduler_paused:
            return
        if not self._supports_background_scheduler():
            return
        self._scheduler_wakeup = asyncio.Event()
        self._scheduler_wakeup.set()
        self._scheduler_task = asyncio.create_task(self._scheduler_loop())

    async def stop_scheduler(self) -> None:
        task = self._scheduler_task
        if task is None:
            return
        self._scheduler_task = None
        self._wake_scheduler()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    async def pause_scheduler(self) -> None:
        self._scheduler_paused = True
        await self.stop_scheduler()

    async def resume_scheduler(self) -> None:
        self._scheduler_paused = False
        await self.start_scheduler()

    async def enqueue(
        self,
        event: RuntimeEvent,
        *,
        wake_background: bool = True,
    ) -> None:
        self._events.append(event)
        self.state.pending_event_ids.append(event.event_id)
        if wake_background:
            self._wake_scheduler()

    async def run_ready(self) -> list[ActionOutcome]:
        outcome = await self.run_once()
        if outcome is None:
            return []
        return [outcome]

    async def run_once(self) -> ActionOutcome | None:
        async with self._run_lock:
            now = self.now()
            try:
                event = self._next_event()
                if event is not None:
                    self.services.memory.record_runtime_event(event)
                    outcome = await self._handle_event(event, now=now)
                    self._record_progress(now=now, outcome=outcome)
                    await self.services.memory.save_snapshot(self.state)
                    return outcome

                if self._needs_day_start_plan(now):
                    synthetic = RuntimeEvent(
                        event_type=EventType.DAY_START,
                        source=EventSource.SYSTEM,
                        created_at=now.isoformat(),
                    )
                    self.services.memory.record_runtime_event(synthetic)
                    outcome = await self._handle_event(synthetic, now=now)
                    self._record_progress(now=now, outcome=outcome)
                    await self.services.memory.save_snapshot(self.state)
                    return outcome

                if self._interaction_cooldown_is_due(now):
                    outcome = await self._handle_interaction_cooldown_expiry(now=now)
                    self._record_progress(now=now, outcome=outcome)
                    await self.services.memory.save_snapshot(self.state)
                    return outcome

                if self._interaction_cooldown_is_active(now):
                    return None

                due_step = self._next_due_step(now)
                if due_step is not None:
                    outcome = await self._execute_step(due_step, event=None, now=now)
                    self._record_progress(now=now, outcome=outcome)
                    await self.services.memory.save_snapshot(self.state)
                    return outcome

                due_block = self._next_due_block(now)
                if due_block is not None:
                    outcome = await self._execute_block(due_block, event=None, now=now)
                    self._record_progress(now=now, outcome=outcome)
                    await self.services.memory.save_snapshot(self.state)
                    return outcome

                if self._uses_hour_granularity():
                    if not self.state.plan.day_blocks:
                        synthetic = RuntimeEvent(
                            event_type=EventType.SYSTEM_BOOT,
                            source=EventSource.SYSTEM,
                            created_at=now.isoformat(),
                        )
                        self.services.memory.record_runtime_event(synthetic)
                        outcome = await self._handle_event(synthetic, now=now)
                        self._record_progress(now=now, outcome=outcome)
                        await self.services.memory.save_snapshot(self.state)
                        return outcome
                elif not self.state.plan.minute_steps:
                    synthetic = RuntimeEvent(
                        event_type=EventType.SYSTEM_BOOT,
                        source=EventSource.SYSTEM,
                        created_at=now.isoformat(),
                    )
                    planner_expand = getattr(self.services.planning, "expand_ready_block", None)
                    if callable(planner_expand):
                        refreshed = await planner_expand(
                            self.state,
                            now=now,
                            trigger_event=synthetic,
                        )
                        if refreshed is not None:
                            self.state.plan = refreshed
                            self.services.memory.update_plan_context(
                                day_blocks=refreshed.day_blocks,
                                plan_date=refreshed.plan_date,
                            )
                            due_step = self._next_due_step(now)
                            if due_step is not None:
                                outcome = await self._execute_step(
                                    due_step,
                                    event=synthetic,
                                    now=now,
                                )
                                self._record_progress(now=now, outcome=outcome)
                                await self.services.memory.save_snapshot(self.state)
                                return outcome
                            self._record_progress(now=now, outcome=None)
                            await self.services.memory.save_snapshot(self.state)
                            return None
                    self.services.memory.record_runtime_event(synthetic)
                    outcome = await self._handle_event(synthetic, now=now)
                    self._record_progress(now=now, outcome=outcome)
                    await self.services.memory.save_snapshot(self.state)
                    return outcome
            except Exception as exc:
                self.state.last_error = str(exc)
                raise

            return None

    def next_pending_step(self) -> PlanStep | None:
        if self._uses_hour_granularity():
            block = self.next_pending_block()
            if block is None:
                return None
            return self._synthetic_step_for_block(block, now=self.now())
        pending = [
            step
            for step in self.state.plan.minute_steps
            if step.status == PlanStepStatus.PENDING
        ]
        if not pending:
            return None
        pending.sort(
            key=lambda step: _parse_dt(step.scheduled_for)
            or datetime.max.replace(tzinfo=UTC)
        )
        return pending[0]

    def next_pending_block(self) -> DayPlanBlock | None:
        active = self._active_block()
        if active is not None and active.status != PlanOutlineStatus.COMPLETE:
            return active
        for block in self.state.plan.day_blocks:
            if block.status != PlanOutlineStatus.COMPLETE:
                return block
        return None

    def has_pending_events(self) -> bool:
        return bool(self._events)

    def next_wake_at(self) -> datetime:
        return self._next_background_wake_at(self.now())

    def started_at(self) -> datetime:
        return self._started_at

    def now(self) -> datetime:
        return self.clock.now()

    def is_processing(self) -> bool:
        return self._run_lock.locked()

    def clock_is_controllable(self) -> bool:
        return self.clock.is_controllable

    def clock_mode(self) -> str:
        if isinstance(self.clock, AdjustableClock):
            return "real_time" if self.clock.tick_real_time else "manual"
        if self._supports_background_scheduler():
            return "real_time"
        return "manual"

    def scheduler_running(self) -> bool:
        return self._scheduler_task is not None and not self._scheduler_task.done()

    def scheduler_paused(self) -> bool:
        return self._scheduler_paused

    def supports_background_scheduler(self) -> bool:
        return self._supports_background_scheduler()

    def set_time(self, value: datetime) -> None:
        self.clock.set(value)
        self._wake_scheduler()

    def advance_time(self, *, minutes: int = 0) -> datetime:
        advanced = self.clock.advance(timedelta(minutes=minutes))
        self._wake_scheduler()
        return advanced

    async def refresh_plan(self) -> RuntimeEvent:
        async with self._run_lock:
            now = self.now()
            refresh_event = RuntimeEvent(
                event_type=EventType.PLAN_REFRESH_REQUESTED,
                source=EventSource.RUNTIME,
                created_at=now.isoformat(),
            )
            self.services.memory.record_runtime_event(refresh_event)
            plan = await self.services.planning.plan_next_window(
                self.state,
                refresh_event,
                now=now,
            )
            self.state.plan = plan
            self.services.memory.update_plan_context(
                day_blocks=plan.day_blocks,
                plan_date=plan.plan_date,
            )
            self._record_progress(now=now, outcome=None)
            await self.services.memory.save_snapshot(self.state)
            self._wake_scheduler()
            return refresh_event

    def _next_event(self) -> RuntimeEvent | None:
        if not self._events:
            return None
        event = self._events.popleft()
        if event.event_id in self.state.pending_event_ids:
            self.state.pending_event_ids.remove(event.event_id)
        return event

    def _next_due_step(self, now: datetime) -> PlanStep | None:
        if self._uses_hour_granularity():
            return None
        for step in self.state.plan.minute_steps:
            if step.status != PlanStepStatus.PENDING:
                continue
            scheduled_for = _parse_dt(step.scheduled_for)
            if scheduled_for is None or scheduled_for <= now:
                return step
            break
        return None

    def _needs_day_start_plan(self, now: datetime) -> bool:
        today = now.date().isoformat()
        plan_date = self.state.plan.plan_date
        return plan_date != today

    def _next_background_wake_at(self, now: datetime) -> datetime:
        if self._events:
            return now
        if self._needs_day_start_plan(now):
            return now
        cooldown_deadline = self._interaction_cooldown_deadline()
        if cooldown_deadline is not None:
            if cooldown_deadline <= now:
                return now
            return min(cooldown_deadline, self._next_midnight(now))
        if self._uses_hour_granularity():
            if not self.state.plan.day_blocks:
                return now
            due_block = self._next_due_block(now)
            if due_block is not None:
                return now
            next_block_at = self._next_block_start(now)
            next_midnight = self._next_midnight(now)
            if next_block_at is None:
                return next_midnight
            return min(next_block_at, next_midnight)
        if not self.state.plan.minute_steps:
            next_block_wake = getattr(self.services.planning, "next_block_wake_at", None)
            if callable(next_block_wake):
                wake_at = next_block_wake(self.state, now=now)
                if wake_at is not None:
                    return wake_at
            return now

        next_step = self.next_pending_step()
        next_step_at = _parse_dt(next_step.scheduled_for) if next_step is not None else None
        next_midnight = self._next_midnight(now)

        if next_step_at is None:
            return next_midnight
        return min(next_step_at, next_midnight)

    async def _handle_event(self, event: RuntimeEvent, *, now: datetime) -> ActionOutcome | None:
        if event.event_type == EventType.MESSAGE_RECEIVED:
            return await self._execute_interaction(event=event, now=now)

        plan = await self.services.planning.plan_next_window(self.state, event, now=now)
        self.state.plan = plan
        self.services.memory.update_plan_context(
            day_blocks=plan.day_blocks,
            plan_date=plan.plan_date,
        )
        if self._uses_hour_granularity():
            due_block = self._next_due_block(now)
            if due_block is None:
                return None
            return await self._execute_block(due_block, event=event, now=now)
        due_step = self._next_due_step(now)
        if due_step is None:
            return None
        return await self._execute_step(due_step, event=event, now=now)

    async def _execute_step(
        self,
        step: PlanStep,
        *,
        event: RuntimeEvent | None,
        now: datetime,
    ) -> ActionOutcome:
        step.status = PlanStepStatus.IN_PROGRESS
        step.started_at = now.isoformat()
        next_pending_step = self.next_pending_step()
        next_step_at = (
            _parse_dt(next_pending_step.scheduled_for) if next_pending_step is not None else None
        )
        outcome = await self.services.execution.execute_step(
            step,
            state=self.state,
            event=event,
            loop_context=ExecutionLoopContext(
                now_provider=self.now,
                next_step_scheduled_for=next_step_at,
                should_interrupt=self.has_pending_events,
            ),
        )
        step.status = PlanStepStatus.COMPLETE
        step.completed_at = now.isoformat()
        self.state.current_action_id = step.step_id
        memory_content = await self.services.memory.summarize_outcome(
            step,
            outcome,
            state=self.state,
            event=event,
        )
        plan_exhausted = self._all_steps_complete()
        self.services.memory.record_outcome(
            step,
            outcome,
            memory_content=memory_content,
            interaction_partner=resolve_interaction_partner(event),
        )
        proactive_result = await self._maybe_execute_proactive_interaction(
            state=self.state,
            outcome=outcome,
        )
        replan_outcome = proactive_result.outcome if proactive_result is not None else outcome
        if proactive_result is not None:
            for message in proactive_result.messages:
                self.services.communication.emit(message)
            recorder = getattr(self.services.memory, "record_interaction", None)
            if callable(recorder):
                recorder(
                    proactive_result.outcome,
                    memory_content=proactive_result.memory_content,
                    interaction_partner=proactive_result.interaction_partner,
                )
            self._begin_interaction_cooldown(
                now=now,
                context=proactive_result.memory_content,
                resume_after_completion=plan_exhausted,
            )
            return replan_outcome
        decision = await self._decide_replan(
            now=now,
            event=event,
            outcome=replan_outcome,
        )
        self._record_replan_decision(
            decision=decision,
            event=event,
            outcome=replan_outcome,
        )

        if decision.kind != ReplanKind.NO_REPLAN:
            await self._apply_replan(
                now=now,
                decision=decision,
                event=event,
                outcome=replan_outcome,
            )
        elif plan_exhausted:
            await self._advance_after_completion(now=now)
        return replan_outcome

    async def _execute_block(
        self,
        block: DayPlanBlock,
        *,
        event: RuntimeEvent | None,
        now: datetime,
    ) -> ActionOutcome:
        step = self._synthetic_step_for_block(block, now=now)
        outcome = await self.services.execution.execute_step(
            step,
            state=self.state,
            event=event,
            loop_context=ExecutionLoopContext(
                now_provider=self.now,
                next_step_scheduled_for=None,
                should_interrupt=self.has_pending_events,
            ),
        )
        self.state.current_action_id = step.step_id
        memory_content = await self.services.memory.summarize_outcome(
            step,
            outcome,
            state=self.state,
            event=event,
        )
        plan_exhausted = self._all_blocks_complete_after(block)
        self.services.memory.record_outcome(
            step,
            outcome,
            memory_content=memory_content,
            interaction_partner=resolve_interaction_partner(event),
        )
        proactive_result = await self._maybe_execute_proactive_interaction(
            state=self.state,
            outcome=outcome,
        )
        replan_outcome = proactive_result.outcome if proactive_result is not None else outcome
        if proactive_result is not None:
            for message in proactive_result.messages:
                self.services.communication.emit(message)
            recorder = getattr(self.services.memory, "record_interaction", None)
            if callable(recorder):
                recorder(
                    proactive_result.outcome,
                    memory_content=proactive_result.memory_content,
                    interaction_partner=proactive_result.interaction_partner,
                )
            self._begin_interaction_cooldown(
                now=now,
                context=proactive_result.memory_content,
                resume_after_completion=plan_exhausted,
            )
            return replan_outcome
        decision = await self._decide_replan(
            now=now,
            event=event,
            outcome=replan_outcome,
        )
        self._record_replan_decision(
            decision=decision,
            event=event,
            outcome=replan_outcome,
        )
        if decision.kind != ReplanKind.NO_REPLAN:
            await self._apply_replan(
                now=now,
                decision=decision,
                event=event,
                outcome=replan_outcome,
            )
        else:
            await self._advance_after_completion(now=now)
        return replan_outcome

    async def _execute_interaction(
        self,
        *,
        event: RuntimeEvent,
        now: datetime,
    ) -> ActionOutcome:
        result: InteractionExecutionResult = await self.services.interaction.execute_interaction(
            event=event,
            state=self.state,
        )
        outcome = result.outcome
        for message in result.messages:
            self.services.communication.emit(message)
        recorder = getattr(self.services.memory, "record_interaction", None)
        if callable(recorder):
            recorder(
                outcome,
                memory_content=result.memory_content,
                interaction_partner=result.interaction_partner,
            )
        self._begin_interaction_cooldown(
            now=now,
            context=result.memory_content,
            resume_after_completion=self.state.interaction_cooldown_resume_after_completion,
        )
        return outcome

    async def _handle_interaction_cooldown_expiry(
        self,
        *,
        now: datetime,
    ) -> ActionOutcome:
        cooldown_until = self.state.interaction_cooldown_until
        context = self.state.interaction_cooldown_context.strip()
        event = RuntimeEvent(
            event_type=EventType.SCHEDULE_WAKE,
            source=EventSource.TIMER,
            created_at=now.isoformat(),
            payload={
                "reason": "interaction_cooldown_expired",
                "cooldown_until": cooldown_until or "",
            },
        )
        self.services.memory.record_runtime_event(event)
        outcome = ActionOutcome(
            action_id=event.event_id,
            status=OutcomeStatus.SUCCESS,
            mode=ExecutionMode.NARRATIVE,
            source=ExecutionZone.NON_REAL,
            content=(
                context
                or "The conversation has paused for now. You turn your attention back to the rest of the day."
            ),
            execution_trace=[
                ExecutionTraceEntry(
                    stage="interaction_cooldown_expired",
                    content=context or "interaction cooldown expired",
                )
            ],
            raw_data={
                "trigger": "interaction_cooldown_expired",
                "interaction_cooldown_context": context,
                "cooldown_until": cooldown_until,
            },
        )
        decision = await self._decide_replan(
            now=now,
            event=event,
            outcome=outcome,
        )
        self._record_replan_decision(
            decision=decision,
            event=event,
            outcome=outcome,
        )
        if decision.kind != ReplanKind.NO_REPLAN:
            await self._apply_replan(
                now=now,
                decision=decision,
                event=event,
                outcome=outcome,
            )
        elif self.state.interaction_cooldown_resume_after_completion:
            await self._advance_after_completion(now=now)
        self._clear_interaction_cooldown()
        return outcome

    async def _maybe_execute_proactive_interaction(
        self,
        *,
        state: RuntimeState,
        outcome: ActionOutcome,
    ) -> InteractionExecutionResult | None:
        raw_data = outcome.raw_data if isinstance(outcome.raw_data, dict) else {}
        stop_reason = str(raw_data.get("loop_stop_reason", "")).strip()
        if stop_reason != "proactive_interaction":
            return None
        payload = raw_data.get("proactive_interaction")
        if not isinstance(payload, dict):
            return None
        name = str(payload.get("name", "")).strip()
        message_content = str(payload.get("message_content", "")).strip()
        if not name or not message_content:
            return None
        executor = getattr(self.services.interaction, "execute_outbound_interaction", None)
        if not callable(executor):
            return None
        return await executor(
            state=state,
            partner_name=name,
            message_text=message_content,
        )

    def _all_steps_complete(self) -> bool:
        return bool(self.state.plan.minute_steps) and all(
            step.status == PlanStepStatus.COMPLETE for step in self.state.plan.minute_steps
        )

    def _all_blocks_complete_after(self, current_block: DayPlanBlock) -> bool:
        remaining = [
            block
            for block in self.state.plan.day_blocks
            if block.status != PlanOutlineStatus.COMPLETE
            and block.block_id != current_block.block_id
        ]
        return not remaining

    async def _decide_replan(
        self,
        *,
        now: datetime,
        event: RuntimeEvent | None,
        outcome: ActionOutcome,
    ) -> ReplanDecision:
        return await self.services.replan.decide(
            now=now,
            state=self.state,
            event=event,
            outcome=outcome,
        )

    def _record_replan_decision(
        self,
        *,
        decision: ReplanDecision,
        event: RuntimeEvent | None,
        outcome: ActionOutcome,
    ) -> None:
        recorder = getattr(self.services.memory, "record_replan_decision", None)
        if recorder is None:
            return
        recorder(
            decision,
            event=event,
            outcome=outcome,
        )

    async def _advance_after_completion(self, *, now: datetime) -> None:
        refresh_event = RuntimeEvent(
            event_type=EventType.ACTION_COMPLETED,
            source=EventSource.RUNTIME,
            created_at=now.isoformat(),
        )
        self.services.memory.record_runtime_event(refresh_event)
        advance = getattr(self.services.planning, "advance_after_completion", None)
        if advance is None:
            refreshed = await self.services.planning.plan_next_window(
                self.state,
                refresh_event,
                now=now,
            )
        else:
            refreshed = await advance(
                self.state,
                now=now,
            )
        self.state.plan = refreshed
        self.services.memory.update_plan_context(
            day_blocks=refreshed.day_blocks,
            plan_date=refreshed.plan_date,
        )

    async def _apply_replan(
        self,
        *,
        now: datetime,
        decision: ReplanDecision,
        event: RuntimeEvent | None,
        outcome: ActionOutcome,
    ) -> None:
        refresh_event = RuntimeEvent(
            event_type=EventType.ACTION_COMPLETED,
            source=EventSource.RUNTIME,
            created_at=now.isoformat(),
            payload={
                "replan_kind": decision.kind.value,
                "reason": decision.reason,
            },
        )
        self.services.memory.record_runtime_event(refresh_event)
        replan = getattr(self.services.planning, "replan_after_completion", None)
        if replan is None:
            refreshed = await self.services.planning.plan_next_window(
                self.state,
                refresh_event,
                now=now,
            )
        else:
            refreshed = await replan(
                self.state,
                now=now,
                kind=decision.kind,
                reason=decision.reason,
                event=event,
                outcome=outcome,
            )
        self.state.plan = refreshed
        self.services.memory.update_plan_context(
            day_blocks=refreshed.day_blocks,
            plan_date=refreshed.plan_date,
        )

    def _record_progress(
        self,
        *,
        now: datetime,
        outcome: ActionOutcome | None,
    ) -> None:
        self.state.last_progress_at = now.isoformat()
        if outcome is None:
            return
        self.state.last_outcome_status = outcome.status
        if outcome.status in (
            OutcomeStatus.RETRYABLE_FAILURE,
            OutcomeStatus.BLOCKED_FAILURE,
        ):
            self.state.last_error = outcome.content
        else:
            self.state.last_error = None

    def _supports_background_scheduler(self) -> bool:
        resolver = getattr(self.clock, "sleep_delay_until", None)
        if resolver is None:
            return False
        return resolver(self.now()) is not None

    def _interaction_cooldown_deadline(self) -> datetime | None:
        return _parse_dt(self.state.interaction_cooldown_until)

    def _interaction_cooldown_is_active(self, now: datetime) -> bool:
        deadline = self._interaction_cooldown_deadline()
        return deadline is not None and deadline > now

    def _interaction_cooldown_is_due(self, now: datetime) -> bool:
        deadline = self._interaction_cooldown_deadline()
        return deadline is not None and deadline <= now

    def _begin_interaction_cooldown(
        self,
        *,
        now: datetime,
        context: str,
        resume_after_completion: bool,
    ) -> None:
        deadline = now + timedelta(seconds=self.interaction_cooldown_seconds)
        self.state.interaction_cooldown_until = deadline.isoformat()
        self.state.interaction_cooldown_context = context.strip()
        self.state.interaction_cooldown_resume_after_completion = resume_after_completion

    def _clear_interaction_cooldown(self) -> None:
        self.state.interaction_cooldown_until = None
        self.state.interaction_cooldown_context = ""
        self.state.interaction_cooldown_resume_after_completion = False

    def _uses_hour_granularity(self) -> bool:
        granularity = getattr(
            self.services.planning,
            "execution_granularity",
            ExecutionGranularity.MINUTE,
        )
        return granularity == ExecutionGranularity.HOUR

    def _active_block(self) -> DayPlanBlock | None:
        for block in self.state.plan.day_blocks:
            if block.block_id == self.state.plan.active_block_id:
                return block
        return None

    def _next_due_block(self, now: datetime) -> DayPlanBlock | None:
        if not self._uses_hour_granularity():
            return None
        block = self._active_block()
        if block is None or block.status == PlanOutlineStatus.COMPLETE:
            return None
        window = self._block_window(block, now=now)
        if window is None:
            return None
        start_at, end_at = window
        if start_at <= now < end_at:
            return block
        return None

    def _next_block_start(self, now: datetime) -> datetime | None:
        starts: list[datetime] = []
        for block in self.state.plan.day_blocks:
            if block.status == PlanOutlineStatus.COMPLETE:
                continue
            window = self._block_window(block, now=now)
            if window is None:
                continue
            start_at, _ = window
            if start_at > now:
                starts.append(start_at)
        if not starts:
            return None
        starts.sort()
        return starts[0]

    def _block_window(self, block: DayPlanBlock, *, now: datetime) -> tuple[datetime, datetime] | None:
        raw = block.time.strip()
        if "-" not in raw:
            return None
        start_raw, end_raw = raw.split("-", 1)
        try:
            start_hour, start_minute = [int(part) for part in start_raw.split(":", 1)]
            end_hour, end_minute = [int(part) for part in end_raw.split(":", 1)]
        except ValueError:
            return None
        start_at = now.replace(hour=start_hour, minute=start_minute, second=0, microsecond=0)
        end_at = now.replace(hour=end_hour, minute=end_minute, second=0, microsecond=0)
        if end_at <= start_at:
            end_at += timedelta(days=1)
        return start_at, end_at

    def _next_midnight(self, now: datetime) -> datetime:
        return datetime.combine(
            (now + timedelta(days=1)).date(),
            datetime.min.time(),
            tzinfo=now.tzinfo,
        )

    def _synthetic_step_for_block(self, block: DayPlanBlock, *, now: datetime) -> PlanStep:
        window = self._block_window(block, now=now)
        minutes = 60
        scheduled_for: str | None = None
        if window is not None:
            start_at, end_at = window
            scheduled_for = start_at.isoformat()
            minutes = max(1, int((end_at - start_at).total_seconds() // 60))
        return PlanStep(
            step_id=f"block_{block.block_id}",
            title=block.label.strip(),
            detail="",
            minutes=minutes,
            scheduled_for=scheduled_for,
        )

    def _wake_scheduler(self) -> None:
        if self._scheduler_wakeup is not None:
            self._scheduler_wakeup.set()

    async def _scheduler_loop(self) -> None:
        assert self._scheduler_wakeup is not None
        wakeup = self._scheduler_wakeup
        while True:
            now = self.now()
            target = self._next_background_wake_at(now)
            if target <= now:
                await self.run_once()
                await asyncio.sleep(0)
                continue

            delay = self.clock.sleep_delay_until(target)
            if delay is None:
                return

            wakeup.clear()
            try:
                await asyncio.wait_for(wakeup.wait(), timeout=delay)
            except TimeoutError:
                pass
