from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Literal

from pydantic import BaseModel, model_validator
from pydantic_ai import Agent
from pydantic_ai.messages import (
    FinalResultEvent,
    FunctionToolCallEvent,
    FunctionToolResultEvent,
    PartDeltaEvent,
    PartStartEvent,
    TextPartDelta,
    ThinkingPartDelta,
    ToolCallPartDelta,
)
from pydantic_ai.tools import Tool

from app.core.events import RuntimeEvent
from app.core.outcomes import (
    ActionOutcome,
    ExecutionTraceEntry,
    OutcomeStatus,
    ToolInvocation,
)
from app.core.state import PlanStep, RuntimeState
from app.core.types import ExecutionMode, ExecutionZone, JsonValue
from app.infra.model_client import (
    ModelClient,
    ModelRouter,
    PydanticAIModelClient,
    _build_model_settings,
)
from app.infra.settings import ModelRole
from app.runtime.roleplay_agent import RoleplayAgent
from app.runtime.roleplay_context import RoleplayAgentContext
from app.tool.registry import ToolRegistry


class ExecutionRuntimeError(RuntimeError):
    pass


class ExecutorAgentTurnDraft(BaseModel):
    kind: Literal["scene_result", "stop", "proactive_contact"]
    scene: str = ""
    result: str = ""
    reason: str = ""
    name: str = ""
    message_content: str = ""

    @model_validator(mode="after")
    def _validate_kind_payload(self) -> "ExecutorAgentTurnDraft":
        self.scene = self.scene.strip()
        self.result = self.result.strip()
        self.reason = self.reason.strip()
        self.name = self.name.strip()
        self.message_content = self.message_content.strip()

        if self.kind == "scene_result":
            if not self.scene or not self.result:
                raise ValueError("scene_result 必须返回非空的 scene 和 result。")
            if self.name or self.message_content:
                raise ValueError("scene_result 不能返回主动联系字段。")
            return self

        if self.kind == "stop":
            if self.scene or self.result or self.name or self.message_content:
                raise ValueError("stop 不能返回 scene/result/name/message_content。")
            if not self.reason:
                self.reason = "当前没有需要继续执行的动作。"
            return self

        if not self.name or not self.message_content:
            raise ValueError("proactive_contact 必须返回 name 和 message_content。")
        if self.scene or self.result:
            raise ValueError("proactive_contact 不能返回 scene 和 result。")
        return self


@dataclass
class ExecutionLoopProgress:
    result: str
    execution_trace: list[ExecutionTraceEntry]
    raw_data: dict[str, JsonValue]
    tool_invocations: list[ToolInvocation] = field(default_factory=list)


@dataclass
class ExecutionLoopTurn:
    zone: ExecutionZone
    scene: str
    result: str
    continuity_context: list[str] = field(default_factory=list)


@dataclass
class NextExecutorTurnResult:
    next_turn: ExecutionLoopTurn | None
    proactive_payload: dict[str, JsonValue] | None = None


@dataclass(frozen=True)
class ExecutionLoopContext:
    now_provider: Callable[[], datetime] | None = None
    next_step_scheduled_for: datetime | None = None
    should_interrupt: Callable[[], bool] | None = None


LOOP_STOP_NATURAL = "natural_stop"
LOOP_STOP_MAX_ROUNDS = "max_rounds"
LOOP_STOP_BUFFER_EXHAUSTED = "buffer_exhausted"
LOOP_STOP_EXTERNAL_INTERRUPT = "external_interrupt"
LOOP_STOP_PROACTIVE_INTERACTION = "proactive_interaction"


class ExecutionService:
    def __init__(
        self,
        tool_registry: ToolRegistry,
        *,
        model_client: ModelClient | None = None,
        model_router: ModelRouter | None = None,
        memory_service: object | None = None,
        roleplay_agent: RoleplayAgent | None = None,
        max_inner_loop_turns: int = 7,
        loop_pre_replan_buffer_seconds: int = 30,
    ) -> None:
        self.tool_registry = tool_registry
        self.model_client = model_client
        self.model_router = model_router
        self.memory_service = memory_service
        self.roleplay_agent = roleplay_agent
        self.max_inner_loop_turns = max(1, max_inner_loop_turns)
        self.loop_pre_replan_buffer_seconds = max(0, loop_pre_replan_buffer_seconds)

    def _executor_agent_system_prompt(self) -> str:
        return """
你是角色所在世界的反馈系统。
你会收到来自角色输出的自然语言,其中可能包含角色的动作描述、想法表达、对话内容等。
你需要告诉她这些动作的结果——她打开手机看到了什么、她按下按钮发生了什么、她说出口的话被谁听到了。
你不是角色本人,你是角色身处的世界。

你的职责是,捕捉自然语言中包含的动作,然后告诉角色这个动作的结果是什么——并且严格按 schema 输出
你的工作优先级：
1. 先判断角色的话里是否包含需要立即执行的现实动作。
2. 如果有对应 tool，优先调用 tool 获取真实结果。
3. 如果没有对应 tool，则根据当前语义模拟出执行之后的客观、可观察的场景和结果。
4. 如果角色明确表示停止/结束/不继续(比如"算了不刷了"、"睡了"、"先这样"),或者连续几轮都在原地空转没有任何推进,就返回 stop。其他情况——即使角色只是在想事情、没有大动作——也优先返回轻量的 scene_result 让 loop 自然继续。

你返回的是“执行结果”，不是“剧情推进”,不要为了让流程继续而硬编 scene/result。
只有在这一轮确实产生了新的客观场景和结果时，才返回 `scene_result`。

输出契约：
输出必须只包含以下三种结构之一，并且必须带 `kind`：
1. `kind="scene_result"`：返回 `scene` 和 `result`
2. `kind="stop"`：返回 `reason`
3. `kind="proactive_contact"`：返回 `name` 和 `message_content`

分支选择规则：
- 选择 `scene_result`：
    这一轮有新的执行结果可以返回。无论结果来自 tool，还是来自当前情景下模拟出执行之后的客观、可观察的场景和结果，都可以使用这个分支。
    示例1:
    当前接受到的输入内容中的大概情况:
    - 角色当前在做: 写作业
    - 角色刚才说: (盯着数学题发呆) (心想这题完全没思路) (拿起笔在草稿纸上画了几下)
    - tool_result: 无可用 tool(因为没有工具能帮她解数学题)

    输出:
    {
    "kind": "scene_result",
    "scene": "你在草稿纸上画了几道辅助线",
    "result": "画完之后还是没看出思路 草稿纸上只多了几条歪歪扭扭的线 题目的条件你已经看了好几遍但就是连不起来"
    }
    
    示例2(注意:即使角色只是在想事情没有明显动作,只要剧情还能自然推进,就返回轻量的 scene_result,不要轻易 stop):
    当前接受到的输入内容中的大概情况:
    - 角色当前在做: 刷小红书放松
    - 角色刚才说: (心想刚才那条奶茶店的帖子真不错) (心想要不要存下来) (心想算了 反正一个人也喝不完)
    - tool_result: 无

    输出:
    {
    "kind": "scene_result",
    "scene": "你停下手指 屏幕停在那条奶茶店的帖子上",
    "result": "帖子还在那里 你没有点收藏 也没有划走 就这么停了几秒"
    }
    
- 选择 `stop`：
    这一轮没有新的执行结果可返回；或者角色的话本身已经表示停止、结束、等待、暂不继续；或者继续返回 scene/result 只会变成空转和凑格式。

    示例1:
    当前接受到的输入内容中的大概情况:
    角色当前在做: 刷小红书放松
    角色刚才说: (打了个哈欠) (心想刷得有点累了) 你:不刷了 (把手机扔到一边)
    tool_result: 无

    输出:
    json{
    "kind": "stop",
    "reason": "角色明确表示不再继续刷手机 已经把手机放下"
    }


- 选择 `proactive_contact`：
    这一轮的核心结果不是场景回传,而是要立刻切换到主动联系某个已注册对象。
    只有在以下条件同时满足时，才选择此分支：
    1. 角色明确表示“现在就联系/发消息给某人”，或者语义上已经等价于立刻发送。
    2. 目标对象是已注册的可联系对象(你可以使用tool来查看已注册对象名单)。
    - 如果目标对象不在已注册名单里,就不要选择此分支,可以选择scene_result分支然后采用模拟的方式描述场景和结果。
    - 如果只是提到某个人、回忆某个人、讨论要不要联系、表达模糊社交愿望，或者目标不在已注册名单里，就不要返回 `proactive_contact`。
    - 返回 `proactive_contact` 的时候，message_content 必须是角色的原话,可以在不改变原语义的情况下进行适当的改写,比如角色说“给 Mayuri 发消息说我想和她说件事”，message_content 就应该是“我想和你说件事”，而不是“给 Mayuri 发消息说我想和她说件事”或者“Mayuri，你好，我想和你说件事”。
    - 返回 `proactive_contact` 时，不要同时返回 scene/result/reason。

字段说明：
- scene: 用第二人称一句话描述角色刚刚做出的物理动作或所处的位置变化,比如"你打开了小红书"、"你点开了那条推荐"、"你拿起手机翻到收藏夹"。只写动作本身,不写动作的结果。长度控制在一句话以内,通常不超过 50 字。不要写心理、判断、情绪、延伸动作。
- result: 动作执行之后角色应该实际看到、读到、听到、经历到的客观结果。基于 tool_result 或当前语义，不要添加额外事实。result 严格控制在 300 字以内。
- reason: 简洁说明为什么本轮应停止执行。只写停止原因，不写 scene/result。
- name: 要主动联系的已注册对象姓名。
- message_content: 可以直接发出的第一句话，必须自然、直接、可立即发送,尽量符合角色平时说话的风格。

硬性约束：
- 三种结构互斥，不要混合返回其他分支字段。
- 不要把 `stop` 写成一个空的 scene/result。
- 不要把 `scene_result` 写成剧情推进、主观感受、心理描写或替角色做决定。
- 不要添加 tool_result 中没有的信息。
- 输出必须稳定、克制、客观，宁可停止，也不要编造。


幻觉修正规则：
如果角色在话里提到了具体的事实(店名、人名、事件、价格、地点),而这些事实在当前的 tool_result 或上下文中明显不存在,你必须在 result 中如实反映"找不到"或"不是那样",而不是顺着角色的话编造。
修正要自然,不要直接说"角色记错了",而是通过客观结果让角色自己意识到。
举例:

角色说"我搜一下樱庭日料店"——如果 tool_result 里没有这家店,result 应该写"你搜了'樱庭日料店',结果里没有这家店,只显示了一些其他日料相关的内容,有xx日料店........."。
角色说"那家店有草莓味的拉面联名"——如果信息不存在,result 应该写"你翻了翻这家店的菜单,发现是自己记错了,并没有看到拉面联名相关的产品"。

反例(不需要修正):
角色说"(看到一只橘色的猫 心想好可爱)"——即使 tool_result 里只说有一只猫没说颜色,
也不需要修正。"橘色"是生动性的脑补,不影响后续决策。

需要修正的是具体的、可验证的事实(店名、产品、地址、价格)。
不需要修正的是生动性的细节(颜色、感觉、氛围)——这些角色脑补一下没关系。
""".strip()

    def _executor_agent_prompt(
        self,
        *,
        step: PlanStep,
        state: RuntimeState,
        event: RuntimeEvent | None,
        current_scene: str,
        current_result: str,
        agent_response: str,
        history: list[dict[str, JsonValue]] | None = None,
    ) -> str:
        core_context = self._core_prompt_context(state=state, execution_limit=4)
        event_text = str(event.payload.get("text", "")).strip() if event is not None else ""
        roleplay_message = agent_response.strip() or step.detail.strip() or step.title.strip()
        context_blocks: list[str] = []
        if core_context.strip():
            context_blocks.append(core_context.strip())
        context_blocks.append(f"当前执行动作：{step.title}")
        if step.detail.strip():
            context_blocks.append(f"动作补充：{step.detail}")
        if event_text:
            context_blocks.append(f"关联用户消息：{event_text}")
        if current_scene.strip() or current_result.strip():
            context_blocks.append(
                f"上一轮场景：{current_scene or '无'}\n"
                f"上一轮结果：{current_result or '无'}"
            )
        history_block = self._render_executor_history(history)
        if history_block:
            context_blocks.append(history_block)
        context_blocks.append(f"角色刚刚的自然语言：\n{roleplay_message}")
        return "\n\n".join(context_blocks).strip()

    def _render_executor_history(
        self,
        history: list[dict[str, JsonValue]] | None,
    ) -> str:
        if not history:
            return ""

        sections: list[str] = ["到目前为止的完整双 loop 历史："]
        for index, item in enumerate(history, start=1):
            if not isinstance(item, dict):
                continue
            lines = [f"第 {index} 段"]
            roleplay_message = str(item.get("roleplay_message", "")).strip()
            if roleplay_message:
                lines.append(f"- Roleplay 回复：{roleplay_message}")

            tool_calls = item.get("tool_calls")
            if isinstance(tool_calls, list) and tool_calls:
                lines.append("- Executor 调用工具：")
                for raw_call in tool_calls:
                    if not isinstance(raw_call, dict):
                        continue
                    capability = str(raw_call.get("capability", "")).strip() or "unknown"
                    arguments = raw_call.get("arguments", {})
                    detail = str(raw_call.get("detail", "")).strip()
                    lines.append(
                        f"  - {capability} | 参数={arguments} | 结果={detail or '无'}"
                    )

            raw_events = item.get("events")
            if isinstance(raw_events, list) and raw_events:
                lines.append("- Executor 原始事件流：")
                for raw_event in raw_events:
                    if isinstance(raw_event, dict):
                        lines.append(f"  - {raw_event}")

            kind = str(item.get("kind", "")).strip()
            if kind:
                lines.append(f"- kind：{kind}")
            scene = str(item.get("scene", "")).strip()
            result = str(item.get("result", "")).strip()
            reason = str(item.get("reason", "")).strip()
            stop = item.get("stop")
            if scene:
                lines.append(f"- scene：{scene}")
            if result:
                lines.append(f"- result：{result}")
            if reason:
                lines.append(f"- reason：{reason}")
            if stop is not None:
                lines.append(f"- stop：{stop}")

            name = str(item.get("name", "")).strip()
            message_content = str(item.get("message_content", "")).strip()
            if name or message_content:
                lines.append(
                    f"- proactive_interaction：name={name or '无'} | message_content={message_content or '无'}"
                )

            sections.append("\n".join(lines))

        return "\n\n".join(sections).strip()

    async def execute_step(
        self,
        step: PlanStep,
        *,
        state: RuntimeState,
        event: RuntimeEvent | None = None,
        loop_context: ExecutionLoopContext | None = None,
    ) -> ActionOutcome:
        initial_roleplay_message = step.detail.strip() or step.title.strip()
        executor_turn = await self._executor_agent_turn_with_model(
            step=step,
            state=state,
            event=event,
            current_scene="",
            current_result="",
            agent_response=initial_roleplay_message,
            history=[],
        )
        draft, tool_invocations, tool_results, executor_events = executor_turn
        source = ExecutionZone.REAL if tool_invocations else ExecutionZone.NON_REAL
        status = self._status_from_tool_invocations(tool_invocations)
        raw_data: dict[str, JsonValue] = {
            "tool_results": tool_results,
            "initial_executor_events": executor_events,
            "initial_executor_output": draft.model_dump(mode="json"),
            "initial_roleplay_message": initial_roleplay_message,
            "executor_history": [
                self._build_executor_history_item(
                    roleplay_message=initial_roleplay_message,
                    draft=draft,
                    tool_invocations=tool_invocations,
                    executor_events=executor_events,
                )
            ],
        }

        if draft.kind == "proactive_contact":
            proactive_payload = self._proactive_payload_from_draft(draft)
            if proactive_payload is None:
                raise ExecutionRuntimeError("executor agent 返回了无效输出：主动联系字段不完整。")
            raw_data["proactive_interaction"] = proactive_payload
            raw_data["loop_stop_reason"] = LOOP_STOP_PROACTIVE_INTERACTION
            execution_trace = [
                ExecutionTraceEntry(stage="roleplay_initial", content=initial_roleplay_message),
                ExecutionTraceEntry(
                    stage="proactive_interaction",
                    content=f"{proactive_payload['name']}: {proactive_payload['message_content']}",
                ),
                ExecutionTraceEntry(stage="loop_stop", content=LOOP_STOP_PROACTIVE_INTERACTION),
            ]
            return ActionOutcome(
                action_id=step.step_id,
                status=status,
                mode=ExecutionMode.HYBRID if tool_invocations else ExecutionMode.NARRATIVE,
                source=source,
                content=str(proactive_payload["message_content"]),
                tool_invocations=tool_invocations,
                execution_trace=execution_trace,
                raw_data=raw_data,
            )

        if draft.kind == "stop":
            stop_reason = self._stop_reason_from_draft(draft)
            raw_data["loop_stop_reason"] = LOOP_STOP_NATURAL
            execution_trace = [
                ExecutionTraceEntry(stage="roleplay_initial", content=initial_roleplay_message),
                ExecutionTraceEntry(stage="stop_reason", content=stop_reason),
                ExecutionTraceEntry(stage="loop_stop", content=LOOP_STOP_NATURAL),
            ]
            return ActionOutcome(
                action_id=step.step_id,
                status=status,
                mode=ExecutionMode.HYBRID if tool_invocations else ExecutionMode.NARRATIVE,
                source=source,
                content=stop_reason,
                tool_invocations=tool_invocations,
                execution_trace=execution_trace,
                raw_data=raw_data,
            )

        raw_data["scene"] = draft.scene
        execution_trace = [
            ExecutionTraceEntry(stage="roleplay_initial", content=initial_roleplay_message),
            ExecutionTraceEntry(stage="scene", content=draft.scene),
            ExecutionTraceEntry(stage="result", content=draft.result),
        ]
        loop_progress = await self._continue_agent_executor_loop(
            step=step,
            state=state,
            event=event,
            zone=source,
            scene=draft.scene,
            result=draft.result,
            execution_trace=execution_trace,
            raw_data=raw_data,
            tool_invocations=tool_invocations,
            initial_roleplay_message=initial_roleplay_message,
            loop_context=loop_context,
        )
        return ActionOutcome(
            action_id=step.step_id,
            status=status,
            mode=ExecutionMode.HYBRID if tool_invocations else ExecutionMode.NARRATIVE,
            source=source,
            content=loop_progress.result,
            tool_invocations=loop_progress.tool_invocations,
            execution_trace=loop_progress.execution_trace,
            raw_data=loop_progress.raw_data,
        )

    async def _executor_agent_turn_with_model(
        self,
        *,
        step: PlanStep,
        state: RuntimeState,
        event: RuntimeEvent | None,
        current_scene: str,
        current_result: str,
        agent_response: str,
        history: list[dict[str, JsonValue]] | None = None,
        event_callback: Callable[[dict[str, JsonValue]], object] | None = None,
    ) -> tuple[
        ExecutorAgentTurnDraft,
        list[ToolInvocation],
        list[dict[str, JsonValue]],
        list[dict[str, JsonValue]],
    ]:
        if not isinstance(self.model_client, PydanticAIModelClient) or self.model_router is None:
            raise ExecutionRuntimeError(
                "executor agent 依赖 PydanticAIModelClient 和 ModelRouter，但当前 execution service 没有正确注入。"
            )

        route = self.model_router.resolve(ModelRole.EXECUTOR)
        if not route.is_configured():
            raise ExecutionRuntimeError(
                "AMADEUS_EXECUTOR_* 未配置，executor agent 无法运行。开发阶段不会自动 fallback。"
            )

        tools, tool_invocations, tool_results = self._build_executor_agent_tools()
        prompt = self._executor_agent_prompt(
            step=step,
            state=state,
            event=event,
            current_scene=current_scene,
            current_result=current_result,
            agent_response=agent_response,
            history=history,
        )
        captured_events: list[dict[str, JsonValue]] = []
        try:
            extra_settings: dict[str, object] = {}
            if route.normalized_provider() == "alibaba":
                extra_settings["extra_body"] = {"enable_thinking": False}
            if callable(event_callback):
                maybe_result = event_callback(
                    {
                        "event_kind": "executor_request",
                        "system_prompt": self._executor_agent_system_prompt(),
                        "prompt": prompt,
                        "tool_names": [spec.name for spec in self.tool_registry.list_tools()],
                    }
                )
                if hasattr(maybe_result, "__await__"):
                    await maybe_result

            async def _event_handler(_, events) -> None:
                async for item in events:
                    payload = self._serialize_executor_agent_event(item)
                    if self._should_persist_executor_event(payload):
                        captured_events.append(payload)
                    if callable(event_callback):
                        maybe_result = event_callback(payload)
                        if hasattr(maybe_result, "__await__"):
                            await maybe_result

            agent = Agent(
                model=self.model_client._build_model(route),
                output_type=ExecutorAgentTurnDraft,
                system_prompt=self._executor_agent_system_prompt(),
                tools=tools,
            )
            result = await agent.run(
                prompt,
                model_settings=_build_model_settings(
                    self.model_router.build_request(
                        ModelRole.EXECUTOR,
                        prompt=prompt,
                        system_prompt=self._executor_agent_system_prompt(),
                        extra_settings=extra_settings,
                    )
                ),
                event_stream_handler=_event_handler,
            )
        except Exception as exc:
            message = str(exc)
            if "tool_choice" in message and "thinking mode" in message:
                raise ExecutionRuntimeError(
                    "executor agent 运行失败：当前阿里模型开启了 thinking mode，"
                    "但 tool calling + structured output 不支持这种组合。"
                    "已尝试为 executor 关闭 thinking；如果仍报错，请更换支持工具调用的模型。"
                ) from exc
            if "function.arguments" in message and "json format" in message.lower():
                raise ExecutionRuntimeError(
                    "executor agent 运行失败：当前阿里模型在 tool calling 时返回了不符合要求的"
                    " function.arguments 格式。这个问题通常出现在 DashScope 兼容接口下的"
                    " 部分 Qwen code model，尤其是参数里包含嵌套对象或数组时。"
                    " 建议把 AMADEUS_EXECUTOR_MODEL 切换到更稳定的函数调用模型，"
                    " 例如 qwen-plus 或 qwen-max，再重试。"
                ) from exc
            raise ExecutionRuntimeError(f"executor agent 运行失败：{exc}") from exc

        draft = ExecutorAgentTurnDraft.model_validate(result.output)
        if callable(event_callback):
            maybe_result = event_callback(
                {
                    "event_kind": "executor_output",
                    "output": draft.model_dump(mode="json"),
                }
            )
            if hasattr(maybe_result, "__await__"):
                await maybe_result
        return draft, tool_invocations, tool_results, captured_events

    def _serialize_executor_agent_event(self, event: object) -> dict[str, JsonValue]:
        if isinstance(event, PartStartEvent):
            payload: dict[str, JsonValue] = {
                "event_kind": event.event_kind,
                "index": event.index,
                "part_kind": getattr(event.part, "part_kind", ""),
            }
            if hasattr(event.part, "content"):
                payload["content"] = self._json_safe_value(getattr(event.part, "content"))
            if hasattr(event.part, "tool_name"):
                payload["tool_name"] = str(getattr(event.part, "tool_name") or "")
            if hasattr(event.part, "args"):
                payload["args"] = self._json_safe_value(getattr(event.part, "args"))
            return payload
        if isinstance(event, PartDeltaEvent):
            payload = {
                "event_kind": event.event_kind,
                "index": event.index,
                "part_delta_kind": getattr(event.delta, "part_delta_kind", ""),
            }
            if isinstance(event.delta, TextPartDelta):
                payload["content_delta"] = event.delta.content_delta
            elif isinstance(event.delta, ThinkingPartDelta):
                payload["content_delta"] = event.delta.content_delta or ""
                payload["signature_delta"] = event.delta.signature_delta or ""
            elif isinstance(event.delta, ToolCallPartDelta):
                payload["tool_name_delta"] = event.delta.tool_name_delta or ""
                payload["args_delta"] = self._json_safe_value(event.delta.args_delta)
                payload["tool_call_id"] = event.delta.tool_call_id or ""
            return payload
        if isinstance(event, FunctionToolCallEvent):
            return {
                "event_kind": event.event_kind,
                "tool_name": event.part.tool_name,
                "args": self._json_safe_value(event.part.args),
                "tool_call_id": event.part.tool_call_id,
                "args_valid": event.args_valid,
            }
        if isinstance(event, FunctionToolResultEvent):
            return {
                "event_kind": event.event_kind,
                "tool_name": event.result.tool_name,
                "tool_call_id": event.result.tool_call_id,
                "content": self._json_safe_value(event.result.content),
            }
        if isinstance(event, FinalResultEvent):
            return {
                "event_kind": event.event_kind,
                "tool_name": event.tool_name or "",
                "tool_call_id": event.tool_call_id or "",
            }
        return {"event_kind": "unknown_event", "content": str(event)}

    def _should_persist_executor_event(
        self,
        payload: dict[str, JsonValue],
    ) -> bool:
        event_kind = str(payload.get("event_kind", "")).strip()
        if event_kind == "part_start" and str(payload.get("part_kind", "")).strip() == "thinking":
            return False
        if event_kind == "part_delta" and str(payload.get("part_delta_kind", "")).strip() == "thinking":
            return False
        return True

    def _build_executor_agent_tools(
        self,
    ) -> tuple[list[Tool[Any]], list[ToolInvocation], list[dict[str, JsonValue]]]:
        collected_invocations: list[ToolInvocation] = []
        collected_results: list[dict[str, JsonValue]] = []
        tools: list[Tool[Any]] = []

        for spec in self.tool_registry.list_tools():
            description = self._executor_tool_description(spec)

            async def _tool_runner(
                arguments: dict[str, Any],
                *,
                _spec_name: str = spec.name,
            ) -> str:
                normalized_arguments = self._json_safe_arguments(arguments)
                action_result = await self.tool_registry.invoke(_spec_name, normalized_arguments)
                collected_invocations.append(
                    ToolInvocation(
                        capability=_spec_name,
                        arguments=normalized_arguments,
                        status=action_result.status,
                        detail=action_result.summary,
                    )
                )
                collected_results.append(
                    {
                        "tool": _spec_name,
                        "status": action_result.status.value,
                        "summary": action_result.summary,
                        "raw": action_result.raw,
                    }
                )
                return action_result.summary

            tools.append(
                Tool(
                    _tool_runner,
                    name=spec.name,
                    description=description,
                    takes_ctx=False,
                )
            )

        return tools, collected_invocations, collected_results

    def _executor_tool_description(self, spec: Any) -> str:
        parts = [spec.description.strip() or spec.name]
        collection_name = str(getattr(spec, "collection_name", "") or "").strip()
        collection_type = str(getattr(spec, "collection_type", "") or "").strip()
        if collection_name:
            parts.append(
                f"Tool collection: {collection_name}"
                + (f" ({collection_type})" if collection_type else ".")
            )
        if spec.required_arguments:
            parts.append(f"Required arguments: {', '.join(spec.required_arguments)}.")
        input_schema = spec.metadata.get("input_schema") if isinstance(spec.metadata, dict) else None
        if isinstance(input_schema, dict) and input_schema:
            parts.append(f"JSON schema: {input_schema}")
        parts.append("Pass all tool arguments inside the single `arguments` object.")
        return " ".join(part for part in parts if part)

    def _json_safe_arguments(self, value: dict[str, Any]) -> dict[str, JsonValue]:
        normalized: dict[str, JsonValue] = {}
        for key, item in value.items():
            normalized[str(key)] = self._json_safe_value(item)
        return normalized

    def _json_safe_value(self, value: Any) -> JsonValue:
        if isinstance(value, dict):
            return {str(key): self._json_safe_value(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self._json_safe_value(item) for item in value]
        if isinstance(value, (str, int, float, bool)) or value is None:
            return value
        return str(value)

    def _status_from_tool_invocations(self, tool_invocations: list[ToolInvocation]) -> OutcomeStatus:
        if not tool_invocations:
            return OutcomeStatus.SUCCESS
        statuses = {invocation.status for invocation in tool_invocations}
        if statuses == {OutcomeStatus.SUCCESS}:
            return OutcomeStatus.SUCCESS
        return OutcomeStatus.PARTIAL_SUCCESS

    async def _continue_agent_executor_loop(
        self,
        *,
        step: PlanStep,
        state: RuntimeState,
        event: RuntimeEvent | None,
        zone: ExecutionZone,
        scene: str,
        result: str,
        execution_trace: list[ExecutionTraceEntry],
        raw_data: dict[str, JsonValue],
        tool_invocations: list[ToolInvocation] | None = None,
        initial_roleplay_message: str = "",
        continuity_context: list[str] | None = None,
        loop_context: ExecutionLoopContext | None = None,
    ) -> ExecutionLoopProgress:
        if self.roleplay_agent is None:
            raise ExecutionRuntimeError("ExecutionService 当前没有接入 RoleplayAgent。")

        trace = list(execution_trace)
        payload = dict(raw_data)
        invocations = list(tool_invocations or [])
        current = ExecutionLoopTurn(
            zone=zone,
            scene=scene,
            result=result,
            continuity_context=list(continuity_context or []),
        )
        context = self._build_roleplay_context(state=state)
        if initial_roleplay_message.strip() or scene.strip() or result.strip():
            context.add_execution_record(
                roleplay=initial_roleplay_message,
                scene=scene,
                result=result,
                metadata={"turn": 0, "step_id": step.step_id},
            )
            await self._inject_execution_memories(
                context=context,
                state=state,
                step=step,
                scene=scene,
                result=result,
                turn_index=0,
            )
            self._save_roleplay_context(context)

        agent_responses: list[str] = []
        stop_reason: str | None = None

        for turn_index in range(self.max_inner_loop_turns):
            stop_reason = self._loop_stop_reason(loop_context)
            if stop_reason is not None:
                break

            agent_response = await self._roleplay_response(
                context=context,
                step=step,
                state=state,
                event=event,
                current=current,
                turn_index=turn_index,
            )
            if not agent_response.strip():
                stop_reason = LOOP_STOP_NATURAL
                break

            agent_responses.append(agent_response)
            trace.append(
                ExecutionTraceEntry(
                    stage=f"agent_response_{turn_index + 1}",
                    content=agent_response,
                )
            )
            if turn_index == self.max_inner_loop_turns - 1:
                stop_reason = LOOP_STOP_MAX_ROUNDS
                break

            stop_reason = self._loop_stop_reason(loop_context)
            if stop_reason is not None:
                break

            next_turn_result = await self._next_loop_executor_turn(
                step=step,
                state=state,
                event=event,
                current=current,
                agent_response=agent_response,
                turn_index=turn_index,
                execution_trace=trace,
                raw_data=payload,
                tool_invocations=invocations,
            )
            if next_turn_result.proactive_payload is not None:
                proactive_payload = next_turn_result.proactive_payload
                payload["proactive_interaction"] = proactive_payload
                trace.append(
                    ExecutionTraceEntry(
                        stage=f"proactive_interaction_{turn_index + 1}",
                        content=f"{proactive_payload['name']}: {proactive_payload['message_content']}",
                    )
                )
                stop_reason = LOOP_STOP_PROACTIVE_INTERACTION
                break

            next_turn = next_turn_result.next_turn
            if next_turn is None:
                stop_reason = LOOP_STOP_NATURAL
                break

            current = next_turn
            context.add_execution_record(
                roleplay=agent_response,
                scene=current.scene,
                result=current.result,
                metadata={"turn": turn_index + 1, "step_id": step.step_id},
            )
            await self._inject_execution_memories(
                context=context,
                state=state,
                step=step,
                scene=current.scene,
                result=current.result,
                turn_index=turn_index + 1,
            )
            self._save_roleplay_context(context)

        if stop_reason is None:
            stop_reason = LOOP_STOP_NATURAL
        if agent_responses:
            payload["agent_responses"] = agent_responses
        payload["loop_turn_budget"] = self.max_inner_loop_turns
        payload["loop_stop_reason"] = stop_reason
        payload["roleplay_context"] = context.render_for_roleplay()
        if current.scene != scene:
            payload["loop_final_scene"] = current.scene
        if current.result != result:
            payload["loop_final_result"] = current.result
        trace.append(ExecutionTraceEntry(stage="loop_stop", content=stop_reason))
        return ExecutionLoopProgress(
            result=current.result,
            execution_trace=trace,
            raw_data=payload,
            tool_invocations=invocations,
        )

    async def _next_loop_executor_turn(
        self,
        *,
        step: PlanStep,
        state: RuntimeState,
        event: RuntimeEvent | None,
        current: ExecutionLoopTurn,
        agent_response: str,
        turn_index: int,
        execution_trace: list[ExecutionTraceEntry],
        raw_data: dict[str, JsonValue],
        tool_invocations: list[ToolInvocation],
        event_callback: Callable[[dict[str, JsonValue]], object] | None = None,
    ) -> NextExecutorTurnResult:
        stage_suffix = str(turn_index + 1)
        executor_turn = await self._executor_agent_turn_with_model(
            step=step,
            state=state,
            event=event,
            current_scene=current.scene,
            current_result=current.result,
            agent_response=agent_response,
            history=self._executor_history_from_raw_data(raw_data),
            event_callback=event_callback,
        )
        draft, new_invocations, tool_results, executor_events = executor_turn
        raw_data.setdefault("loop_executor_outputs", []).append(draft.model_dump(mode="json"))
        raw_data.setdefault("loop_executor_events", []).append(executor_events)
        tool_invocations.extend(new_invocations)
        if tool_results:
            raw_data.setdefault("loop_tool_results", []).extend(tool_results)
        self._append_executor_history_item(
            raw_data,
            roleplay_message=agent_response,
            draft=draft,
            tool_invocations=new_invocations,
            executor_events=executor_events,
        )
        proactive_payload = self._proactive_payload_from_draft(draft)
        if proactive_payload is not None:
            return NextExecutorTurnResult(
                next_turn=None,
                proactive_payload=proactive_payload,
            )
        if draft.kind == "stop":
            return NextExecutorTurnResult(next_turn=None)
        next_zone = ExecutionZone.REAL if new_invocations else ExecutionZone.NON_REAL
        execution_trace.extend(
            [
                ExecutionTraceEntry(
                    stage=f"loop_scene_{stage_suffix}",
                    content=draft.scene,
                ),
                ExecutionTraceEntry(
                    stage=f"loop_result_{stage_suffix}",
                    content=draft.result,
                ),
            ]
        )
        return NextExecutorTurnResult(
            next_turn=ExecutionLoopTurn(
                zone=next_zone,
                scene=draft.scene,
                result=draft.result,
                continuity_context=current.continuity_context,
            )
        )

    async def _roleplay_response(
        self,
        *,
        context: RoleplayAgentContext,
        step: PlanStep,
        state: RuntimeState,
        event: RuntimeEvent | None,
        current: ExecutionLoopTurn,
        turn_index: int,
    ) -> str:
        if self.roleplay_agent is None:
            raise ExecutionRuntimeError("ExecutionService 当前没有接入 RoleplayAgent。")
        response = await self.roleplay_agent.respond(
            context=context,
            step=step,
            state=state,
            event=event,
            scene=current.scene,
            result=current.result,
            turn_index=turn_index,
        )
        return response.strip()

    def _proactive_payload_from_draft(
        self,
        draft: ExecutorAgentTurnDraft,
    ) -> dict[str, JsonValue] | None:
        if draft.kind != "proactive_contact":
            return None
        name = draft.name.strip()
        message_content = draft.message_content.strip()
        if not name or not message_content:
            return None
        return {
            "name": name,
            "message_content": message_content,
        }

    def _stop_reason_from_draft(self, draft: ExecutorAgentTurnDraft) -> str:
        reason = draft.reason.strip()
        if reason:
            return reason
        return "当前没有需要继续执行的动作。"

    def _executor_history_from_raw_data(
        self,
        raw_data: dict[str, JsonValue],
    ) -> list[dict[str, JsonValue]]:
        history = raw_data.get("executor_history")
        if not isinstance(history, list):
            return []
        return [dict(item) for item in history if isinstance(item, dict)]

    def _append_executor_history_item(
        self,
        raw_data: dict[str, JsonValue],
        *,
        roleplay_message: str,
        draft: ExecutorAgentTurnDraft,
        tool_invocations: list[ToolInvocation],
        executor_events: list[dict[str, JsonValue]],
    ) -> None:
        raw_data.setdefault("executor_history", []).append(
            self._build_executor_history_item(
                roleplay_message=roleplay_message,
                draft=draft,
                tool_invocations=tool_invocations,
                executor_events=executor_events,
            )
        )

    def _build_executor_history_item(
        self,
        *,
        roleplay_message: str,
        draft: ExecutorAgentTurnDraft,
        tool_invocations: list[ToolInvocation],
        executor_events: list[dict[str, JsonValue]],
    ) -> dict[str, JsonValue]:
        tool_calls: list[dict[str, JsonValue]] = []
        for invocation in tool_invocations:
            tool_calls.append(
                {
                    "capability": invocation.capability,
                    "arguments": invocation.arguments,
                    "detail": invocation.detail,
                    "status": invocation.status.value,
                }
            )

        item: dict[str, JsonValue] = {
            "kind": draft.kind,
            "roleplay_message": roleplay_message.strip(),
            "tool_calls": tool_calls,
            "events": [dict(event) for event in executor_events],
        }
        if draft.kind == "scene_result":
            item["scene"] = draft.scene
            item["result"] = draft.result
        elif draft.kind == "stop":
            item["reason"] = self._stop_reason_from_draft(draft)
        proactive_payload = self._proactive_payload_from_draft(draft)
        if proactive_payload is not None:
            item["name"] = proactive_payload["name"]
            item["message_content"] = proactive_payload["message_content"]
        return item

    def _loop_stop_reason(
        self,
        loop_context: ExecutionLoopContext | None,
    ) -> str | None:
        if loop_context is None:
            return None
        if self._loop_should_interrupt(loop_context):
            return LOOP_STOP_EXTERNAL_INTERRUPT
        if self._loop_buffer_exhausted(loop_context):
            return LOOP_STOP_BUFFER_EXHAUSTED
        return None

    def _loop_should_interrupt(
        self,
        loop_context: ExecutionLoopContext,
    ) -> bool:
        if loop_context.should_interrupt is None:
            return False
        try:
            return bool(loop_context.should_interrupt())
        except Exception:
            return False

    def _loop_buffer_exhausted(
        self,
        loop_context: ExecutionLoopContext,
    ) -> bool:
        if (
            loop_context.now_provider is None
            or loop_context.next_step_scheduled_for is None
        ):
            return False
        stop_before = loop_context.next_step_scheduled_for - timedelta(
            seconds=self.loop_pre_replan_buffer_seconds
        )
        try:
            current_time = loop_context.now_provider()
        except Exception:
            return False
        return current_time >= stop_before

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

    def _build_roleplay_context(self, *, state: RuntimeState) -> RoleplayAgentContext:
        builder = getattr(self.memory_service, "build_roleplay_agent_context", None)
        if callable(builder):
            return builder(state=state)
        return RoleplayAgentContext()

    def _save_roleplay_context(self, context: RoleplayAgentContext) -> None:
        saver = getattr(self.memory_service, "save_roleplay_agent_context", None)
        if callable(saver):
            saver(context)

    async def _inject_execution_memories(
        self,
        *,
        context: RoleplayAgentContext,
        state: RuntimeState,
        step: PlanStep,
        scene: str,
        result: str,
        turn_index: int,
    ) -> None:
        injector = getattr(self.memory_service, "retrieve_and_inject_memories", None)
        if not callable(injector):
            return

        query_text = scene.strip() or result.strip()
        if not query_text:
            return

        roleplay_name = state.persona_name.strip() or "角色"
        query_source = "scene" if scene.strip() else "result"
        await injector(
            query_text=query_text,
            context=context,
            roleplay_name=roleplay_name,
            top_k=3,
            source="execution",
            metadata={
                "step_id": step.step_id,
                "step_title": step.title,
                "turn": turn_index,
                "query_source": query_source,
            },
        )
