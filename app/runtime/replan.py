from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from app.core.events import RuntimeEvent
from app.core.outcomes import ActionOutcome, ReplanDecision, ReplanKind
from app.core.state import RuntimeState
from app.infra.model_client import ModelClient, ModelRouter, StructuredResponse
from app.infra.settings import ModelRole


class ReplanDecisionDraft(BaseModel):
    decision: Literal["no_replan", "micro_replan", "hour_replan"]
    reason: str = ""


class ReplanService:
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

    def _build_decision_prompt(
        self,
        *,
        now: datetime | None,
        state: RuntimeState | None,
        event: RuntimeEvent | None,
        outcome: ActionOutcome,
        memory_context: list[str],
    ) -> str:
        event_name = event.event_type.value if event is not None else "none"
        event_text = str(event.payload.get("text", "")).strip() if event is not None else ""
        current_time = (
            now.isoformat()
            if now is not None
            else (event.created_at if event is not None else "[unknown]")
        )
        active_block_summary = self._active_block_summary(state)
        retrieved_memory_lines = [f"- {item}" for item in memory_context if item.strip()]
        if not retrieved_memory_lines:
            retrieved_memory_lines = ["- [none]"]
        retrieved_memory_section = (
            "和这次重规划判断最相关的检索记忆：\n"
            + "\n".join(retrieved_memory_lines)
        )
        raw_outcome_section = (
            "这次触发重规划判断的原始结果：\n"
            f"- 触发事件类型：{event_name}\n"
            f"- 关联事件文本：{event_text or '[无]'}\n"
            f"- 结果内容：{outcome.content}"
        )

        sections = [f"当前时间：{current_time}"]
        if active_block_summary:
            sections.append(f"当前激活时间段：{active_block_summary}")
        sections.extend([retrieved_memory_section, raw_outcome_section])
        return "\n".join(section for section in sections if section.strip())

    def _build_decision_system_prompt(self, *, state: RuntimeState | None) -> str:
        core_memory_section = self._core_prompt_context(state=state).rstrip()
        decision_rules_section = """
请你根据上面的核心记忆，结合 user message 里的检索记忆和这次触发判断的原始结果，判断现在是否需要重规划，并直接输出 JSON 对象。
要求：
JSON 对象中必须包含 `decision` 和 `reason`
`decision` 只能是 `no_replan`、`micro_replan`、`hour_replan` 之一
`no_replan` 表示保持当前计划不变，只做正常继续或正常推进
`micro_replan` 表示只需要改变当前时间段里剩余的动作计划
`hour_replan` 表示不但需要改变当前时间段里剩余的动作计划，还需要改变当前时间段以后的计划
`reason` 请给出简短、具体的中文原因
注意：这是对“当前时点之后”是否需要调整计划的判断，不是对过去结果的总结
""".strip()
        return "\n".join(
            section for section in [core_memory_section, decision_rules_section] if section
        )

    def _core_prompt_context(self, *, state: RuntimeState | None) -> str:
        builder = getattr(self.memory_service, "core_prompt_context", None)
        if builder is None:
            return ""
        return builder(state=state, execution_limit=6)

    async def decide(
        self,
        *,
        now: datetime | None = None,
        state: RuntimeState | None,
        event: RuntimeEvent | None,
        outcome: ActionOutcome,
    ) -> ReplanDecision:
        memory_context = await self._memory_context(
            state=state,
            event=event,
            outcome=outcome,
        )
        return await self._decide_with_model(
            now=now,
            state=state,
            event=event,
            outcome=outcome,
            memory_context=memory_context,
        )

    async def _decide_with_model(
        self,
        *,
        now: datetime | None,
        state: RuntimeState | None,
        event: RuntimeEvent | None,
        outcome: ActionOutcome,
        memory_context: list[str],
    ) -> ReplanDecision:
        self._decision_route_or_raise(purpose="replan decision")
        request = self.model_router.build_request(
            ModelRole.DECISION,
            prompt=self._build_decision_prompt(
                now=now,
                state=state,
                event=event,
                outcome=outcome,
                memory_context=memory_context,
            ),
            system_prompt=self._build_decision_system_prompt(state=state),
        )
        try:
            response: StructuredResponse[ReplanDecisionDraft] = (
                await self.model_client.generate_structured(
                    request,
                    ReplanDecisionDraft,
                )
            )
        except Exception as exc:
            raise RuntimeError("The replan decision model call failed.") from exc

        return ReplanDecision(
            kind=ReplanKind(response.structured.decision),
            reason=response.structured.reason.strip(),
            confidence=None,
            source="structured_model",
        )

    async def _memory_context(
        self,
        *,
        state: RuntimeState | None,
        event: RuntimeEvent | None,
        outcome: ActionOutcome,
    ) -> list[str]:
        if self.memory_service is None:
            return []

        resolver = getattr(self.memory_service, "replan_memory_context", None)
        if resolver is None:
            return []

        query_parts = [outcome.content.strip()]
        if state is not None:
            query_parts.extend(
                [
                    self._active_block_label(state),
                    state.plan.day_summary.strip(),
                ]
            )
        if event is not None:
            query_parts.append(str(event.payload.get("text", "")).strip())

        query_text = " ".join(part for part in query_parts if part)
        try:
            memories = await resolver(query_text=query_text)
        except Exception:
            return []
        if not isinstance(memories, list):
            return []
        return [str(item).strip() for item in memories if str(item).strip()]

    def _active_block_label(self, state: RuntimeState) -> str:
        for block in state.plan.day_blocks:
            if block.block_id == state.plan.active_block_id:
                return block.label.strip()
        return ""

    def _active_block_summary(self, state: RuntimeState | None) -> str:
        if state is None:
            return ""
        for block in state.plan.day_blocks:
            if block.block_id == state.plan.active_block_id:
                return f"{block.time} {block.label} [{block.status.value}]"
        return ""

    def _decision_route_or_raise(self, *, purpose: str):
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
        return route
