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

    # 这里刻意把所有 prompt 构造函数放在文件前面，方便集中调整。
    # 总原则和 planning 保持一致：
    # 1. 核心记忆和稳定规则放进 system prompt
    # 2. 当前这次判断相关的动态上下文放进 user prompt

    def _build_decision_prompt(
        self,
        *,
        now: datetime | None,
        state: RuntimeState | None,
        event: RuntimeEvent | None,
        outcome: ActionOutcome,
        plan_exhausted: bool,
        memory_context: list[str],
    ) -> str:
        event_name = event.event_type.value if event is not None else "none"
        event_text = ""
        current_hour_summary = ""
        active_block_summary = "none"
        if event is not None:
            event_text = str(event.payload.get("text", "")).strip()
        if state is not None:
            current_hour_summary = state.plan.current_hour_summary
            for block in state.plan.day_blocks:
                block_summary = f"{block.time} {block.label} [{block.status.value}]"
                if block.block_id == state.plan.active_block_id:
                    active_block_summary = block_summary

        # 这部分是“当前时间”，方便模型知道判断发生在今天的哪个时点。
        current_time = (
            now.isoformat()
            if now is not None
            else (event.created_at if event is not None else "[未知]")
        )

        # 这部分是“当前时间”文本块。
        current_time_section = f"当前时间：{current_time}"

        # 这部分是“当前激活时间段”文本块。
        # 如果你后面觉得只保留 active block 就够，可以只拼这个，删掉下面的当前时段摘要。
        current_active_block_section = ""
        if active_block_summary != "none":
            current_active_block_section = f"当前激活时间段：{active_block_summary}"

        # 这部分是“当前时段摘要”文本块。
        current_hour_summary_section = ""
        if current_hour_summary.strip():
            current_hour_summary_section = f"当前时段摘要：{current_hour_summary.strip()}"

        # 这部分是“检索记忆”文本块。
        # 这里放的是和本次 replan 判断最相关的历史记忆，不是最新一次 outcome 的压缩摘要。
        retrieved_memory_lines = [f"- {item}" for item in memory_context if item.strip()]
        if not retrieved_memory_lines:
            retrieved_memory_lines = ["- [无]"]
        retrieved_memory_section = (
            "和这次重规划判断最相关的检索记忆：\n"
            + "\n".join(retrieved_memory_lines)
        )

        # 这部分是“最新一次触发 replan 的原始结果”文本块。
        # 这里明确使用 execution / interaction 刚产出的原始 outcome，而不是 summarize 后写回记忆的版本。
        raw_outcome_section = (
            "这次触发重规划判断的原始结果：\n"
            f"- 触发事件类型：{event_name}\n"
            f"- 关联事件文本：{event_text or '[无]'}\n"
            f"- 结果状态：{outcome.status.value}\n"
            f"- 结果内容：{outcome.content}\n"
            f"- 当前分钟窗口是否耗尽：{plan_exhausted}"
        )

        # 这里才是真正的 user prompt。
        # 你后面如果要自己调结构，只需要改这些变量在 f-string 里的顺序。
        user_prompt = f"""
{current_time_section}
{current_active_block_section}
{current_hour_summary_section}
{retrieved_memory_section}
{raw_outcome_section}
""".strip()
        return user_prompt

    def _build_decision_system_prompt(self, *, state: RuntimeState | None) -> str:
        # 这部分是“核心记忆”文本块。
        # 当前由 memory_service.core_prompt_context() 统一提供，里面已经包含 soul.md、今日计划、今日已执行事件/结果。
        core_memory_section = self._core_prompt_context(state=state).rstrip()

        # 这部分是“规则说明”文本块。
        # 这里只放稳定规则和输出格式要求，方便你后面单独微调这段。
        decision_rules_section = """
请你根据上面的核心记忆，结合 user message 里提供的检索记忆和这次触发判断的原始结果，判断现在是否需要重规划，并直接输出 JSON 对象。
要求:
JSON 对象中必须包含 `decision` 和 `reason`
`decision` 只能是 `no_replan`、`micro_replan`、`hour_replan` 之一
`no_replan` 表示保持当前计划不变，只做正常继续或正常推进
`micro_replan` 表示只需要改变当前时间段里剩余的动作计划
`hour_replan` 表示不但需要改变当前时间段里剩余的动作计划,还需要改变当前时间段以后的计划
`reason` 请给出简短、具体的中文原因
注意：这是对“当前时点之后”是否需要调整计划的判断，不是对过去结果的总结
""".strip()

        # 这里才是真正的 system prompt。
        # 你后面如果想把规则拆得更细，或者把核心记忆换顺序，直接改这个 f-string 就行。
        system_prompt = f"""
{core_memory_section}
{decision_rules_section}
""".strip()
        return system_prompt

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
        plan_exhausted: bool = False,
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
            plan_exhausted=plan_exhausted,
            memory_context=memory_context,
        )

    async def _decide_with_model(
        self,
        *,
        now: datetime | None,
        state: RuntimeState | None,
        event: RuntimeEvent | None,
        outcome: ActionOutcome,
        plan_exhausted: bool,
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
                plan_exhausted=plan_exhausted,
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
                    state.plan.current_hour_summary.strip(),
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
