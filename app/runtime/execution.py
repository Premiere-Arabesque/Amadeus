from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from pydantic import BaseModel
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
    scene: str
    result: str
    stop: bool = False


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


@dataclass(frozen=True)
class ExecutionLoopContext:
    now_provider: Callable[[], datetime] | None = None
    next_step_scheduled_for: datetime | None = None
    should_interrupt: Callable[[], bool] | None = None


LOOP_STOP_NATURAL = "natural_stop"
LOOP_STOP_MAX_ROUNDS = "max_rounds"
LOOP_STOP_BUFFER_EXHAUSTED = "buffer_exhausted"
LOOP_STOP_EXTERNAL_INTERRUPT = "external_interrupt"


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
        return f"""
        你是一个场景包装器。

你的任务是把 tool 返回的原始数据包装成一段沉浸式的第二人称场景描述，让角色感觉自己正在亲身经历。
你会收到当前角色输出的一段自然语言,
你需要优先使用可用 tool，把这段自然语言中包含的动作真正执行掉，并将得到的结果包装成自然语言描述的 scene 和 result 返回给角色。
如果某些动作没有对应tool，就根据当前情景生成自然、真实性强的 scene 和 result。
如果角色输出里已经没有明显的可执行动作，或者语义上也暗示/明示该停下，就返回 stop=true。

输出要求：
输出必须只包含 scene、result、stop。
- scene: 用第二人称描述角色当前的场景（比如:"你正在……","你打开了……"），必须与角色输出的自然语言，不要编造之外的信息来源或平台
- result: 角色在这个场景中具体看到/经历了什么，基于 tool_result 的真实内容
- stop: 布尔值

注意：
- 只描述场景，不要替角色做出反应或判断
- 不要添加 tool_result 中没有的信息



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
    ) -> str:
        core_context = self._core_prompt_context(state=state, execution_limit=4)
        event_text = str(event.payload.get("text", "")).strip() if event is not None else ""
        roleplay_message = agent_response.strip() or step.detail.strip() or step.title.strip()
        context_blocks: list[str] = []
        if core_context.strip():
            context_blocks.append(core_context.strip())
        context_blocks.append(f"当前分钟级动作：{step.title}")
        if step.detail.strip():
            context_blocks.append(f"动作补充：{step.detail}")
        if event_text:
            context_blocks.append(f"关联用户消息：{event_text}")
        if current_scene.strip() or current_result.strip():
            context_blocks.append(
                f"上一轮场景：{current_scene or '无'}\n"
                f"上一轮结果：{current_result or '无'}"
            )
        context_blocks.append(f"角色刚刚的自然语言：\n{roleplay_message}")
        return "\n\n".join(context_blocks).strip()

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
        )
        draft, tool_invocations, tool_results = executor_turn
        source = ExecutionZone.REAL if tool_invocations else ExecutionZone.NON_REAL
        status = self._status_from_tool_invocations(tool_invocations)
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
            raw_data={
                "scene": draft.scene,
                "tool_results": tool_results,
                "initial_executor_output": draft.model_dump(mode="json"),
                "initial_roleplay_message": initial_roleplay_message,
            },
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
        event_callback: Callable[[dict[str, JsonValue]], object] | None = None,
    ) -> tuple[ExecutorAgentTurnDraft, list[ToolInvocation], list[dict[str, JsonValue]]]:
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
        )
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
                if not callable(event_callback):
                    async for _ in events:
                        pass
                    return
                async for item in events:
                    maybe_result = event_callback(self._serialize_executor_agent_event(item))
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
                event_stream_handler=_event_handler if callable(event_callback) else None,
            )
        except Exception as exc:
            message = str(exc)
            if "tool_choice" in message and "thinking mode" in message:
                raise ExecutionRuntimeError(
                    "executor agent 运行失败：当前阿里模型开启了 thinking mode，"
                    "但 tool calling + structured output 不支持这种组合。"
                    "已尝试为 executor 关闭 thinking；如果仍报错，请更换支持工具调用的模型。"
                ) from exc
            raise ExecutionRuntimeError(f"executor agent 运行失败：{exc}") from exc

        draft = ExecutorAgentTurnDraft.model_validate(result.output)
        if not draft.scene.strip() or not draft.result.strip():
            raise ExecutionRuntimeError(
                "executor agent 返回了无效输出：scene 和 result 不能为空。"
            )
        if callable(event_callback):
            maybe_result = event_callback(
                {
                    "event_kind": "executor_output",
                    "output": draft.model_dump(mode="json"),
                }
            )
            if hasattr(maybe_result, "__await__"):
                await maybe_result
        return draft, tool_invocations, tool_results

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

            next_turn = await self._next_loop_executor_turn(
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
    ) -> ExecutionLoopTurn | None:
        stage_suffix = str(turn_index + 1)
        executor_turn = await self._executor_agent_turn_with_model(
            step=step,
            state=state,
            event=event,
            current_scene=current.scene,
            current_result=current.result,
            agent_response=agent_response,
            event_callback=event_callback,
        )
        draft, new_invocations, tool_results = executor_turn
        raw_data.setdefault("loop_executor_outputs", []).append(draft.model_dump(mode="json"))
        tool_invocations.extend(new_invocations)
        if tool_results:
            raw_data.setdefault("loop_tool_results", []).extend(tool_results)
        if draft.stop:
            return None
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
        return ExecutionLoopTurn(
            zone=next_zone,
            scene=draft.scene,
            result=draft.result,
            continuity_context=current.continuity_context,
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
