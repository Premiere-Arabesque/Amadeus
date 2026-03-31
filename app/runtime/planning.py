from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta

from pydantic import BaseModel, Field

from app.core.events import EventSource, EventType, RuntimeEvent
from app.core.outcomes import ReplanKind
from app.core.state import (
    DayPlanBlock,
    PlanOutlineItem,
    PlanOutlineStatus,
    PlanState,
    PlanStep,
    RuntimeState,
)
from app.core.types import ExecutionMode, ExecutionZone, JsonValue
from app.infra.model_client import ModelClient, ModelRouter, StructuredResponse
from app.infra.settings import ModelRole

_BLOCK_EXPANSION_LEAD = timedelta(minutes=30)


class PlannedOutlineItemDraft(BaseModel):
    summary: str


class DayPlanBlockDraft(BaseModel):
    time: str
    label: str


class DayPlanDraft(BaseModel):
    items: list[DayPlanBlockDraft] = Field(default_factory=list)


class MinuteActionDraft(BaseModel):
    action_description: str
    duration_minutes: int = Field(default=5, ge=1, le=60)


class MinuteActionPlanDraft(BaseModel):
    items: list[MinuteActionDraft] = Field(default_factory=list)


@dataclass(slots=True)
class PlanningModelTrace:
    scope: str
    prompt: str
    system_prompt: str
    structured_output: dict[str, JsonValue] | None = None
    error: str | None = None


class PlanningService:
    def __init__(
        self,
        *,
        model_client: ModelClient | None = None,
        model_router: ModelRouter | None = None,
        memory_service: object | None = None,
        prompt_store: object | None = None,
    ) -> None:
        del prompt_store
        self.model_client = model_client
        self.model_router = model_router
        self.memory_service = memory_service

    # 这里刻意把所有 prompt 构造函数放在文件前面，方便集中调整。
    # 总原则是：稳定的角色设定和输出格式要求放进 system prompt；
    # 当前时间、记忆、replan 原因这类每轮都会变化的内容放进 user prompt。
    def _build_day_start_prompt(
        self,
        *,
        state: RuntimeState,
        now: datetime,
    ) -> str:
        del state
        day_start_memories = self._day_start_memories(now=now)
        lines = [f"当前时间：{now.isoformat()}"]
        if day_start_memories:
            lines.append(
                "昨天发生了这些事情："
                + "；".join(memory for memory in day_start_memories if memory.strip())
            )
        return "\n".join(line for line in lines if line.strip())

    def _build_block_expand_prompt(
        self,
        *,
        state: RuntimeState,
        now: datetime,
        block: DayPlanBlock,
        reason: str = "",
    ) -> str:
        lines = [
            self._core_prompt_context(state=state, execution_limit=8).rstrip(),
            f"当前时间：{now.isoformat()}",
            f"现在到了 {block.time} 的“{block.label}”时段。",
        ]
        if reason.strip():
            lines.append(f"这次展开还需要考虑这个调整原因：{reason.strip()}")
        return "\n".join(line for line in lines if line.strip())

    def _build_hour_replan_prompt(
        self,
        *,
        state: RuntimeState,
        now: datetime,
        reason: str,
    ) -> str:
        remaining_blocks = self._remaining_day_blocks(state.plan, now=now)
        lines = [
            self._core_prompt_context(state=state, execution_limit=8).rstrip(),
            f"当前时间：{now.isoformat()}",
        ]
        if remaining_blocks:
            lines.append(
                "今天从现在开始剩余的时间块：\n"
                + "\n".join(
                    f"- {block.time} {block.label} [{block.status.value}]"
                    for block in remaining_blocks
                )
            )
        if reason.strip():
            lines.append(f"这次重规划的原因：{reason.strip()}")
        return "\n".join(line for line in lines if line.strip())

    def _build_day_start_system_prompt(self, *, state: RuntimeState) -> str:
        return self._build_role_system_prompt(
            state=state,
            instruction=(
                "请你规划一下这一天从早到晚的安排，并直接输出 JSON 数组。"
                """
                要求:
                每一项都包含 `time` 和 `label`
                `time` 必须使用 `HH:MM-HH:MM`格式   
                `label`为自然语言描述,注意这是未来规划你只知道你计划会做什么事情,描述里不应该有任何基于已经完成此事前提下的体验以及事实描述
                """
            ),
        )

    def _build_block_expand_system_prompt(self, *, state: RuntimeState) -> str:
        return self._build_role_system_prompt(
            state=state,
            instruction=(
                "请你把这个时间段展开成2-6个具体执行的动作，每个动作之间相互独立，"
                "直接输出 JSON 数组,每一项都包含 `action_description` 和 `duration_minutes`。"
                "action_description为自然语言描述,注意这是未来规划你只知道你计划会做什么事情,描述里不应该有任何基于已经完成此事前提下的体验以及事实描述"
            ),
        )

    def _build_hour_replan_system_prompt(self, *, state: RuntimeState) -> str:
        return self._build_role_system_prompt(
            state=state,
            instruction=(
                "请你从当前时间开始，重新规划今天剩余的时间块，并直接输出 JSON 数组。"
                """要求:每一项都包含 `time` 和 `label`
                `time` 必须使用 `HH:MM-HH:MM`格式   
                `label`为自然语言描述,注意这是未来规划你只知道你计划会做什么事情,描述里不应该有任何基于已经完成此事前提下的体验以及事实描述"""
            ),
        )

    def _build_role_system_prompt(self, *, state: RuntimeState, instruction: str) -> str:
        persona_name = state.persona_name.strip() or "Amadeus"
        soul_md = self._soul_markdown().strip()
        parts = [f"你是{persona_name}"]
        if soul_md:
            parts.append(soul_md)
        if instruction.strip():
            parts.append(instruction.strip())
        return "\n".join(part for part in parts if part.strip())

    def _core_prompt_context(
        self,
        *,
        state: RuntimeState,
        execution_limit: int | None = None,
    ) -> str:
        builder = getattr(self.memory_service, "core_prompt_context", None)
        if builder is None:
            return ""
        return builder(state=state, execution_limit=execution_limit)

    def _day_start_memories(self, *, now: datetime) -> list[str]:
        if self.memory_service is None:
            return []
        resolver = getattr(self.memory_service, "day_start_memory_context", None)
        if resolver is None:
            return []
        try:
            memories = resolver(now=now)
        except Exception:
            return []
        if not isinstance(memories, list):
            return []
        return [str(item).strip() for item in memories if str(item).strip()]

    def _soul_markdown(self) -> str:
        if self.memory_service is not None:
            core_memory = getattr(self.memory_service, "core_memory", None)
            if core_memory is not None:
                soul_md = str(getattr(core_memory, "soul_md", "") or "").strip()
                if soul_md:
                    return soul_md
        return ""

    async def plan_next_window(
        self,
        state: RuntimeState,
        trigger_event: RuntimeEvent,
        *,
        now: datetime,
    ) -> PlanState:
        if trigger_event.event_type == EventType.MESSAGE_RECEIVED:
            raise RuntimeError(
                "Message interrupt planning no longer supports heuristic fallback. "
                "Please implement a model-backed message planning flow."
            )

        if trigger_event.event_type == EventType.DAY_START:
            return await self._plan_day_start_with_model(
                state,
                trigger_event,
                now=now,
            )

        if trigger_event.event_type == EventType.ACTION_COMPLETED:
            return await self.advance_after_completion(state, now=now)

        expanded = await self.expand_ready_block(
            state,
            now=now,
            trigger_event=trigger_event,
        )
        if expanded is not None:
            return expanded

        if trigger_event.event_type in {
            EventType.SYSTEM_BOOT,
            EventType.PLAN_REFRESH_REQUESTED,
        }:
            return await self._plan_day_start_with_model(
                state,
                RuntimeEvent(
                    event_type=EventType.DAY_START,
                    source=trigger_event.source,
                    created_at=trigger_event.created_at,
                    payload=trigger_event.payload,
                ),
                now=now,
            )
        raise RuntimeError(
            "No expandable block was available, and heuristic planning is disabled."
        )

    async def advance_after_completion(
        self,
        state: RuntimeState,
        *,
        now: datetime,
    ) -> PlanState:
        advanced = self._advance_existing_layers(state, now=now)
        if advanced is not None:
            expanded = await self.expand_ready_block(
                state.model_copy(update={"plan": advanced}, deep=True),
                trigger_event=RuntimeEvent(
                    event_type=EventType.ACTION_COMPLETED,
                    source=EventSource.RUNTIME,
                    created_at=now.isoformat(),
                ),
                now=now,
            )
            return expanded or advanced

        expanded = await self.expand_ready_block(
            state,
            now=now,
            trigger_event=RuntimeEvent(
                event_type=EventType.ACTION_COMPLETED,
                source=EventSource.RUNTIME,
                created_at=now.isoformat(),
            ),
        )
        if expanded is not None:
            return expanded
        raise RuntimeError(
            "Execution completed, but no model-backed continuation plan could be produced."
        )

    async def replan_after_completion(
        self,
        state: RuntimeState,
        *,
        now: datetime,
        kind: ReplanKind,
        reason: str = "",
        event: RuntimeEvent | None = None,
        outcome: object | None = None,
    ) -> PlanState:
        if kind == ReplanKind.NO_REPLAN:
            return state.plan.model_copy(deep=True)

        if kind == ReplanKind.HOUR_REPLAN:
            return await self._plan_hour_replan_with_model(
                state,
                now=now,
                reason=reason,
                trigger_event=event,
                outcome=outcome,
            )
        model_plan = await self._plan_replan_with_model(
            state=state,
            now=now,
            kind=kind,
            reason=reason,
            event=event,
            outcome=outcome,
        )
        if model_plan is None:
            raise RuntimeError(
                "Replanning was requested with kind "
                f"`{kind.value}`, but no model-backed plan was produced."
            )
        return model_plan

    async def _plan_day_start_with_model(
        self,
        state: RuntimeState,
        trigger_event: RuntimeEvent,
        *,
        now: datetime,
    ) -> PlanState | None:
        self._decision_route_or_raise(purpose="day-start planning")

        system_prompt = self._build_day_start_system_prompt(state=state)
        request = self.model_router.build_request(
            ModelRole.DECISION,
            prompt=self._build_day_start_prompt(state=state, now=now),
            system_prompt=system_prompt,
        )
        try:
            response: StructuredResponse[DayPlanDraft] = (
                await self.model_client.generate_structured(
                    request,
                    DayPlanDraft,
                )
            )
        except Exception as exc:
            self._record_model_trace(
                PlanningModelTrace(
                    scope=f"day_blocks:{trigger_event.event_type.value}",
                    prompt=request.prompt,
                    system_prompt=system_prompt,
                    error=str(exc),
                ),
                trigger_event=trigger_event,
                metadata={"stage": "day_blocks_generation"},
            )
            raise RuntimeError("Model-backed day-start planning failed.") from exc

        plan = self._day_plan_draft_to_plan_state(response.structured.items, now=now)
        expanded = await self._expand_ready_block_with_model(
            state=state,
            plan=plan,
            now=now,
            trigger_event=trigger_event,
            force=False,
        )
        final_plan = expanded or plan
        self._record_model_trace(
            PlanningModelTrace(
                scope=f"day_blocks:{trigger_event.event_type.value}",
                prompt=request.prompt,
                system_prompt=system_prompt,
                structured_output=response.structured.model_dump(mode="json"),
            ),
            trigger_event=trigger_event,
            plan=final_plan,
            metadata={
                "stage": "day_blocks_generation",
                "expanded_ready_block": expanded is not None,
            },
        )
        return final_plan

    async def expand_ready_block(
        self,
        state: RuntimeState,
        *,
        now: datetime,
        trigger_event: RuntimeEvent | None = None,
        force: bool = False,
        reason: str = "",
    ) -> PlanState | None:
        plan = state.plan.model_copy(deep=True)
        self._normalize_day_blocks(plan, now=now)
        if not plan.day_blocks:
            return None

        expanded = await self._expand_ready_block_with_model(
            state=state,
            plan=plan,
            now=now,
            trigger_event=trigger_event,
            force=force,
            reason=reason,
        )
        if expanded is not None:
            return expanded
        return None

    async def expand_specific_block(
        self,
        state: RuntimeState,
        *,
        block_id: str,
        now: datetime,
        trigger_event: RuntimeEvent | None = None,
        reason: str = "",
    ) -> PlanState | None:
        plan = state.plan.model_copy(deep=True)
        self._normalize_day_blocks(plan, now=now)
        if not plan.day_blocks:
            return None

        target = next((block for block in plan.day_blocks if block.block_id == block_id), None)
        if target is None:
            return None

        # 切换到指定时间块时，先丢弃旧分钟窗口，避免仍被上一次展开结果占住。
        plan.minute_steps = []
        plan.current_hour_summary = ""
        plan.hour_plan_items = []
        plan.active_hour_item_id = None
        plan.hour_starts_at = None
        self._set_active_block(plan, block_id)

        state_for_expand = state.model_copy(deep=True)
        state_for_expand.plan = plan
        return await self._expand_ready_block_with_model(
            state=state_for_expand,
            plan=plan,
            now=now,
            trigger_event=trigger_event,
            force=True,
            reason=reason,
        )

    def next_block_wake_at(
        self,
        state: RuntimeState,
        *,
        now: datetime,
    ) -> datetime | None:
        plan = state.plan.model_copy(deep=True)
        self._normalize_day_blocks(plan, now=now)
        if plan.minute_steps:
            return None
        active_block = self._active_day_block(plan)
        if active_block is None:
            return None
        window = self._block_window(active_block, now=now)
        if window is None:
            return None
        start_at, _ = window
        wake_at = start_at - _BLOCK_EXPANSION_LEAD
        return now if wake_at <= now else wake_at

    async def _expand_ready_block_with_model(
        self,
        *,
        state: RuntimeState,
        plan: PlanState,
        now: datetime,
        trigger_event: RuntimeEvent | None,
        force: bool,
        reason: str = "",
    ) -> PlanState | None:
        candidate = self._expandable_day_block(plan, now=now, force=force)
        if candidate is None:
            return None
        self._decision_route_or_raise(
            purpose=f"expand block `{candidate.time} {candidate.label}`",
        )

        system_prompt = self._build_block_expand_system_prompt(state=state)
        state_for_prompt = state.model_copy(deep=True)
        state_for_prompt.plan = plan.model_copy(deep=True)
        request = self.model_router.build_request(
            ModelRole.DECISION,
            prompt=self._build_block_expand_prompt(
                state=state_for_prompt,
                now=now,
                block=candidate,
                reason=reason,
            ),
            system_prompt=system_prompt,
        )
        try:
            response: StructuredResponse[MinuteActionPlanDraft] = (
                await self.model_client.generate_structured(
                    request,
                    MinuteActionPlanDraft,
                )
            )
        except Exception as exc:
            self._record_model_trace(
                PlanningModelTrace(
                    scope=f"block_expand:{candidate.block_id}",
                    prompt=request.prompt,
                    system_prompt=system_prompt,
                    error=str(exc),
                ),
                trigger_event=trigger_event,
                metadata={
                    "stage": "block_expand",
                    "block_time": candidate.time,
                    "block_label": candidate.label,
                },
            )
            raise RuntimeError(
                f"Model-backed block expansion failed for `{candidate.time} {candidate.label}`."
            ) from exc

        expanded = self._plan_with_expanded_block(
            plan=plan,
            block=candidate,
            now=now,
            actions=response.structured.items,
        )
        self._record_model_trace(
            PlanningModelTrace(
                scope=f"block_expand:{candidate.block_id}",
                prompt=request.prompt,
                system_prompt=system_prompt,
                structured_output=response.structured.model_dump(mode="json"),
            ),
            trigger_event=trigger_event,
            plan=expanded,
            metadata={
                "stage": "block_expand",
                "block_time": candidate.time,
                "block_label": candidate.label,
            },
        )
        return expanded

    async def _plan_replan_with_model(
        self,
        *,
        state: RuntimeState,
        now: datetime,
        kind: ReplanKind,
        reason: str,
        event: RuntimeEvent | None,
        outcome: object | None,
    ) -> PlanState | None:
        if kind == ReplanKind.HOUR_REPLAN:
            return await self._plan_hour_replan_with_model(
                state,
                now=now,
                reason=reason,
                trigger_event=event,
                outcome=outcome,
            )
        del outcome
        return await self.expand_ready_block(
            state,
            now=now,
            trigger_event=event,
            force=True,
            reason=reason,
        )

    async def _plan_hour_replan_with_model(
        self,
        state: RuntimeState,
        *,
        now: datetime,
        reason: str,
        trigger_event: RuntimeEvent | None,
        outcome: object | None,
    ) -> PlanState | None:
        del outcome
        self._decision_route_or_raise(purpose="hour-level replanning")

        system_prompt = self._build_hour_replan_system_prompt(state=state)
        request = self.model_router.build_request(
            ModelRole.DECISION,
            prompt=self._build_hour_replan_prompt(
                state=state,
                now=now,
                reason=reason,
            ),
            system_prompt=system_prompt,
        )
        try:
            response: StructuredResponse[DayPlanDraft] = (
                await self.model_client.generate_structured(
                    request,
                    DayPlanDraft,
                )
            )
        except Exception as exc:
            self._record_model_trace(
                PlanningModelTrace(
                    scope="hour_replan",
                    prompt=request.prompt,
                    system_prompt=system_prompt,
                    error=str(exc),
                ),
                trigger_event=trigger_event,
                metadata={"reason": reason, "stage": "hour_replan_generation"},
            )
            raise RuntimeError("Model-backed hour replanning failed.") from exc

        replacement_blocks = self._blocks_from_draft_blocks(
            response.structured.items,
        )
        plan = self._replace_remaining_blocks(
            state.plan,
            now=now,
            replacement_blocks=replacement_blocks,
        )
        expanded = await self._expand_ready_block_with_model(
            state=state,
            plan=plan,
            now=now,
            trigger_event=trigger_event,
            force=True,
            reason=reason,
        )
        final_plan = expanded or plan
        self._record_model_trace(
            PlanningModelTrace(
                scope="hour_replan",
                prompt=request.prompt,
                system_prompt=system_prompt,
                structured_output=response.structured.model_dump(mode="json"),
            ),
            trigger_event=trigger_event,
            plan=final_plan,
            metadata={"reason": reason, "stage": "hour_replan_generation"},
        )
        return final_plan

    def _day_plan_draft_to_plan_state(
        self,
        draft_blocks: list[DayPlanBlockDraft],
        *,
        now: datetime,
    ) -> PlanState:
        blocks = self._blocks_from_draft_blocks(draft_blocks)
        plan = PlanState(
            plan_date=now.date().isoformat(),
            day_summary=self._summarize_day_blocks(blocks),
            day_blocks=blocks,
            minute_steps=[],
        )
        self._normalize_day_blocks(plan, now=now)
        return plan

    def _blocks_from_draft_blocks(
        self,
        draft_blocks: list[DayPlanBlockDraft],
    ) -> list[DayPlanBlock]:
        blocks = [
            DayPlanBlock(time=block.time.strip(), label=block.label.strip())
            for block in draft_blocks
            if block.time.strip() and block.label.strip()
        ]
        if blocks:
            return blocks
        raise RuntimeError("The model returned no valid day blocks.")

    def _replace_remaining_blocks(
        self,
        plan: PlanState,
        *,
        now: datetime,
        replacement_blocks: list[DayPlanBlock],
    ) -> PlanState:
        replanned = plan.model_copy(deep=True)
        preserved_blocks: list[DayPlanBlock] = []
        for block in replanned.day_blocks:
            window = self._block_window(block, now=now)
            if window is None:
                continue
            _, end_at = window
            if end_at <= now:
                block.status = PlanOutlineStatus.COMPLETE
                preserved_blocks.append(block)
        if not replacement_blocks:
            raise RuntimeError(
                "The model returned no remaining time blocks for hour-level replanning."
            )
        replanned.day_blocks = preserved_blocks + [
            block.model_copy(deep=True) for block in replacement_blocks
        ]
        replanned.plan_date = now.date().isoformat()
        replanned.minute_steps = []
        replanned.current_hour_summary = ""
        replanned.hour_plan_items = []
        replanned.active_hour_item_id = None
        replanned.hour_starts_at = None
        replanned.active_block_id = None
        self._normalize_day_blocks(replanned, now=now)
        return replanned

    def _advance_existing_layers(self, state: RuntimeState, *, now: datetime) -> PlanState | None:
        plan = state.plan.model_copy(deep=True)
        self._normalize_day_blocks(plan, now=now)
        if not plan.day_blocks:
            return None
        self._complete_active_day_block(plan, now=now)
        self._normalize_day_blocks(plan, now=now)
        plan.minute_steps = []
        plan.current_hour_summary = ""
        plan.hour_plan_items = []
        plan.active_hour_item_id = None
        plan.hour_starts_at = None
        return plan

    def _plan_with_expanded_block(
        self,
        *,
        plan: PlanState,
        block: DayPlanBlock,
        now: datetime,
        actions: list[MinuteActionDraft],
    ) -> PlanState:
        expanded = plan.model_copy(deep=True)
        self._set_active_block(expanded, block.block_id)
        block_start, _ = self._block_window(block, now=now) or (now, now)
        start_at = max(now, block_start)
        minute_steps = self._actions_to_plan_steps(actions, start_at=start_at)
        if not minute_steps:
            raise RuntimeError(
                f"The model returned no valid minute actions for `{block.time} {block.label}`."
            )
        expanded.plan_date = now.date().isoformat()
        expanded.day_summary = self._summarize_day_blocks(expanded.day_blocks)
        expanded.minute_steps = minute_steps
        expanded.current_hour_summary = block.label
        expanded.hour_plan_items = [PlanOutlineItem(item_id=block.block_id, summary=block.label)]
        expanded.hour_plan_items[0].status = PlanOutlineStatus.ACTIVE
        expanded.active_hour_item_id = block.block_id
        expanded.hour_starts_at = now.isoformat()
        self._sync_outline_from_blocks(expanded)
        return expanded

    def _remaining_day_blocks(
        self,
        plan: PlanState,
        *,
        now: datetime,
    ) -> list[DayPlanBlock]:
        remaining: list[DayPlanBlock] = []
        for block in plan.day_blocks:
            window = self._block_window(block, now=now)
            if window is None:
                continue
            _, end_at = window
            if end_at > now:
                remaining.append(block)
        return remaining

    def _summarize_day_blocks(self, blocks: list[DayPlanBlock]) -> str:
        labels = [block.label.strip() for block in blocks if block.label.strip()]
        if not labels:
            return ""
        preview = "；".join(labels[:4])
        if len(labels) > 4:
            preview = f"{preview}；……"
        return preview

    def _normalize_day_blocks(self, plan: PlanState, *, now: datetime) -> None:
        if not plan.day_blocks:
            self._bootstrap_day_blocks_from_legacy_plan(plan, now=now)
        if not plan.day_blocks:
            plan.day_plan_items = []
            plan.active_day_item_id = None
            plan.active_block_id = None
            return

        active_id: str | None = None
        first_pending_id: str | None = None
        for block in plan.day_blocks:
            if block.status == PlanOutlineStatus.COMPLETE:
                continue
            window = self._block_window(block, now=now)
            if window is None:
                continue
            start_at, end_at = window
            if end_at <= now:
                block.status = PlanOutlineStatus.COMPLETE
                continue
            if first_pending_id is None:
                first_pending_id = block.block_id
            if start_at <= now < end_at:
                block.status = PlanOutlineStatus.ACTIVE
                active_id = block.block_id
                continue
            if active_id is None and first_pending_id == block.block_id:
                block.status = PlanOutlineStatus.ACTIVE
                active_id = block.block_id
            else:
                block.status = PlanOutlineStatus.PENDING

        if active_id is None and first_pending_id is not None:
            active_id = first_pending_id
            for block in plan.day_blocks:
                if block.block_id == active_id:
                    block.status = PlanOutlineStatus.ACTIVE
                    break

        plan.plan_date = now.date().isoformat()
        plan.active_block_id = active_id
        plan.day_summary = self._summarize_day_blocks(plan.day_blocks)
        self._sync_outline_from_blocks(plan)
        if plan.active_block_id is None:
            plan.current_hour_summary = ""
            plan.hour_plan_items = []
            plan.active_hour_item_id = None

    def _bootstrap_day_blocks_from_legacy_plan(self, plan: PlanState, *, now: datetime) -> None:
        labels: list[str] = []
        status_by_label: dict[str, PlanOutlineStatus] = {}
        active_label: str | None = None

        if plan.hour_plan_items:
            for item in plan.hour_plan_items:
                if item.summary.strip():
                    labels.append(item.summary.strip())
                    status_by_label[item.summary.strip()] = item.status
                    if (
                        item.item_id == plan.active_hour_item_id
                        or item.status == PlanOutlineStatus.ACTIVE
                    ):
                        active_label = item.summary.strip()
        elif plan.current_hour_summary.strip():
            labels.append(plan.current_hour_summary.strip())
            active_label = plan.current_hour_summary.strip()
        elif plan.day_plan_items:
            for item in plan.day_plan_items:
                if item.summary.strip():
                    labels.append(item.summary.strip())
                    status_by_label[item.summary.strip()] = item.status
                    if (
                        item.item_id == plan.active_day_item_id
                        or item.status == PlanOutlineStatus.ACTIVE
                    ):
                        active_label = item.summary.strip()
        elif plan.day_summary.strip():
            labels.append(plan.day_summary.strip())
            active_label = plan.day_summary.strip()

        if not labels:
            return

        cursor = now
        blocks: list[DayPlanBlock] = []
        for index, label in enumerate(labels):
            start_at = cursor
            end_at = cursor + timedelta(minutes=90)
            block = DayPlanBlock(
                time=f"{start_at.strftime('%H:%M')}-{end_at.strftime('%H:%M')}",
                label=label,
                status=status_by_label.get(label, PlanOutlineStatus.PENDING),
            )
            if index == 0 and active_label is None:
                block.status = PlanOutlineStatus.ACTIVE
                active_label = label
            blocks.append(block)
            cursor = end_at

        if active_label is not None:
            for block in blocks:
                if block.label == active_label:
                    block.status = PlanOutlineStatus.ACTIVE
                elif block.status != PlanOutlineStatus.COMPLETE:
                    block.status = PlanOutlineStatus.PENDING
        plan.day_blocks = blocks

    def _sync_outline_from_blocks(self, plan: PlanState) -> None:
        plan.day_plan_items = [
            PlanOutlineItem(
                item_id=block.block_id,
                summary=f"{block.time} {block.label}",
                status=block.status,
            )
            for block in plan.day_blocks
        ]
        plan.active_day_item_id = plan.active_block_id

    def _complete_active_day_block(self, plan: PlanState, *, now: datetime) -> None:
        active = self._active_day_block(plan)
        if active is not None:
            active.status = PlanOutlineStatus.COMPLETE
        plan.active_block_id = None
        self._normalize_day_blocks(plan, now=now)

    def _set_active_block(self, plan: PlanState, block_id: str) -> None:
        plan.active_block_id = block_id
        for block in plan.day_blocks:
            if block.block_id == block_id:
                block.status = PlanOutlineStatus.ACTIVE
            elif block.status != PlanOutlineStatus.COMPLETE:
                block.status = PlanOutlineStatus.PENDING

    def _active_day_block(self, plan: PlanState) -> DayPlanBlock | None:
        for block in plan.day_blocks:
            if block.block_id == plan.active_block_id:
                return block
        return None

    def _expandable_day_block(
        self,
        plan: PlanState,
        *,
        now: datetime,
        force: bool,
    ) -> DayPlanBlock | None:
        active = self._active_day_block(plan)
        if active is None:
            return None
        window = self._block_window(active, now=now)
        if window is None:
            return None
        start_at, _ = window
        if force or start_at - _BLOCK_EXPANSION_LEAD <= now:
            return active
        return None

    def _block_window(
        self,
        block: DayPlanBlock,
        *,
        now: datetime,
    ) -> tuple[datetime, datetime] | None:
        try:
            start_text, end_text = [part.strip() for part in block.time.split("-", maxsplit=1)]
            start_time = time.fromisoformat(start_text)
            end_time = time.fromisoformat(end_text)
        except Exception:
            return None
        start_at = datetime.combine(now.date(), start_time, tzinfo=now.tzinfo)
        end_at = datetime.combine(now.date(), end_time, tzinfo=now.tzinfo)
        if end_at <= start_at:
            end_at += timedelta(days=1)
        return start_at, end_at

    def _actions_to_plan_steps(
        self,
        actions: list[MinuteActionDraft],
        *,
        start_at: datetime,
    ) -> list[PlanStep]:
        steps: list[PlanStep] = []
        cursor = start_at
        for action in actions:
            description = action.action_description.strip()
            if not description:
                continue
            steps.append(
                PlanStep(
                    title=self._step_title_from_action(description),
                    detail=description,
                    minutes=action.duration_minutes,
                    execution_mode=ExecutionMode.NARRATIVE,
                    zone_hint=ExecutionZone.NON_REAL,
                    scheduled_for=cursor.isoformat(),
                )
            )
            cursor += timedelta(minutes=action.duration_minutes)
        return steps

    def _step_title_from_action(self, description: str) -> str:
        compact = " ".join(description.split()).strip()
        if len(compact) <= 18:
            return compact
        return f"{compact[:18].rstrip()}..."

    def _decision_route_or_raise(self, *, purpose: str) -> None:
        if self.model_client is None or self.model_router is None:
            raise RuntimeError(
                "A decision model is required for "
                f"{purpose}, but the model runtime is not available."
            )
        route = self.model_router.resolve(ModelRole.DECISION)
        if not route.is_configured():
            raise RuntimeError(
                "A decision model is required for "
                f"{purpose}, but the decision route is not configured."
            )

    def _record_model_trace(
        self,
        trace: PlanningModelTrace,
        *,
        trigger_event: RuntimeEvent | None,
        plan: PlanState | None = None,
        metadata: dict[str, JsonValue] | None = None,
    ) -> None:
        recorder = getattr(self.memory_service, "record_planning_trace", None)
        if recorder is None:
            return
        recorder(
            plan_scope=trace.scope,
            strategy="model" if trace.error is None else "model_error",
            trigger_event=trigger_event,
            plan_state=plan,
            prompt=trace.prompt,
            system_prompt=trace.system_prompt,
            structured_output=trace.structured_output,
            error=trace.error,
            metadata=metadata,
        )
