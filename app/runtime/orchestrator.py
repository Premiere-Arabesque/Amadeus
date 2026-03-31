from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from app.core.events import EventSource, EventType, RuntimeEvent
from app.core.outcomes import ActionOutcome, OutcomeStatus, ReplanDecision, ReplanKind
from app.core.state import PlanStep, PlanStepStatus, RuntimeState
from app.runtime.clock import AdjustableClock, FunctionClock, RuntimeClock
from app.runtime.execution import ExecutionLoopContext
from app.runtime.interaction import resolve_interaction_partner


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
    ) -> None:
        self.services = services
        self.state = initial_state or RuntimeState()
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

                due_step = self._next_due_step(now)
                if due_step is not None:
                    outcome = await self._execute_step(due_step, event=None, now=now)
                    self._record_progress(now=now, outcome=outcome)
                    await self.services.memory.save_snapshot(self.state)
                    return outcome

                if not self.state.plan.minute_steps:
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
        if not self.state.plan.minute_steps:
            next_block_wake = getattr(self.services.planning, "next_block_wake_at", None)
            if callable(next_block_wake):
                wake_at = next_block_wake(self.state, now=now)
                if wake_at is not None:
                    return wake_at
            return now

        next_step = self.next_pending_step()
        next_step_at = _parse_dt(next_step.scheduled_for) if next_step is not None else None
        next_midnight = datetime.combine(
            (now + timedelta(days=1)).date(),
            datetime.min.time(),
            tzinfo=now.tzinfo,
        )

        if next_step_at is None:
            return next_midnight
        return min(next_step_at, next_midnight)

    async def _handle_event(self, event: RuntimeEvent, *, now: datetime) -> ActionOutcome | None:
        plan = await self.services.planning.plan_next_window(self.state, event, now=now)
        self.state.plan = plan
        self.services.memory.update_plan_context(
            day_blocks=plan.day_blocks,
            plan_date=plan.plan_date,
        )
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
        decision = await self._decide_replan(
            now=now,
            event=event,
            outcome=outcome,
            plan_exhausted=plan_exhausted,
        )
        messages = await self.services.interaction.build_messages(
            event=event,
            outcome=outcome,
            state=self.state,
        )
        for message in messages:
            self.services.communication.emit(message)
        self.services.memory.record_outcome(
            step,
            outcome,
            memory_content=memory_content,
            interaction_partner=resolve_interaction_partner(event),
        )
        self._record_replan_decision(
            decision=decision,
            event=event,
            outcome=outcome,
            plan_exhausted=plan_exhausted,
        )

        if decision.kind != ReplanKind.NO_REPLAN:
            await self._apply_replan(
                now=now,
                decision=decision,
                event=event,
                outcome=outcome,
            )
        elif plan_exhausted:
            await self._advance_after_completion(now=now)
        return outcome

    def _all_steps_complete(self) -> bool:
        return bool(self.state.plan.minute_steps) and all(
            step.status == PlanStepStatus.COMPLETE for step in self.state.plan.minute_steps
        )

    async def _decide_replan(
        self,
        *,
        now: datetime,
        event: RuntimeEvent | None,
        outcome: ActionOutcome,
        plan_exhausted: bool,
    ) -> ReplanDecision:
        return await self.services.replan.decide(
            now=now,
            state=self.state,
            event=event,
            outcome=outcome,
            plan_exhausted=plan_exhausted,
        )

    def _record_replan_decision(
        self,
        *,
        decision: ReplanDecision,
        event: RuntimeEvent | None,
        outcome: ActionOutcome,
        plan_exhausted: bool,
    ) -> None:
        recorder = getattr(self.services.memory, "record_replan_decision", None)
        if recorder is None:
            return
        recorder(
            decision,
            event=event,
            outcome=outcome,
            plan_exhausted=plan_exhausted,
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
