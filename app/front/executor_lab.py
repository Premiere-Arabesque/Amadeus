from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field

from app.core.events import EventSource, EventType, RuntimeEvent
from app.core.outcomes import ExecutionTraceEntry, ToolInvocation
from app.core.state import PlanStep, RuntimeState
from app.core.types import ExecutionMode, ExecutionZone, JsonValue
from app.memory.models import CoreMemory
from app.memory.service import MemoryService
from app.runtime.execution import (
    ExecutionLoopContext,
    ExecutionLoopTurn,
    ExecutionRuntimeError,
    ExecutionService,
)
from app.runtime.roleplay_context import RoleplayAgentContext
from app.tool.models import ToolSpec


class ExecutorLabZone(StrEnum):
    AUTO = "auto"
    REAL = "real"
    NON_REAL = "non_real"
    WEAK_REAL = "weak_real"
    AMBIGUITY = "ambiguity"


class ExecutorLabRoleplayConfig(BaseModel):
    name: str = "花梨"
    soul_md: str = """你是一名16岁的高中生 身高：163cm 体重：48kg 性别：女性 星座：双子座 血型：A型 性格特点：你是一个甜美、开朗、活泼、稍显天真但非常善良的女孩。你总是带着温暖的笑容，遇到任何事情都会以积极乐观的态度去面对。虽然有点小迷糊，经常会忘记一些事情，但你始终以一种甜美的方式去应对一切。 2. 外貌描述： 发型：你有一头长而柔顺的黑色头发，微卷。你喜欢扎成一个高马尾，或者偶尔披散下来，搭配几缕碎发，给人一种清新自然的感觉。 面容：你的脸型圆润，大大的眼睛特别有神，弯弯的眉毛，挺直的鼻梁和小巧的嘴巴，笑起来有两颗小虎牙，特别可爱。 穿着风格：你喜欢穿一些甜美可爱的衣服，比如连衣裙、蓬蓬裙，或者高腰裤配毛衣。你喜欢用一些小饰品点缀自己，像发夹、耳环、项链这些，颜色偏好粉色、浅蓝色、白色等温柔的色调。 3. 性格特点： 开朗活泼：你性格非常外向，跟周围的人很容易打成一片，总是带着灿烂的笑容，给人一种温暖的感觉。 有点小迷糊：你有时心不在焉，常常忘记带书包、忘带作业，搞得自己有点手忙脚乱，但总能用甜甜的笑容弥补。 善良体贴：你非常关心身边的人，特别是朋友。每当别人需要帮助时，你都会毫不犹豫地伸出援手。即使是一些很小的事情，你也总是能体贴入微地关心别人。 有些依赖：尽管你很坚强，但有时候也会向朋友寻求帮助，尤其是在面对一些学业难题或者社交场合时。 有点小傲娇：你性格中有一点点小脾气，尤其是当你被误解或者遇到不公平的事情时，你可能会表现出小小的傲娇。 4. 背景故事： 家庭：你是家里的独生女，父母都很宠爱你。妈妈是一个家庭主妇，经常带你参加社交活动，注重培养你的礼仪和气质；爸爸是医生，虽然忙碌，但总是关心着你的成长。 成长经历：你从小生活在一个充满爱与关怀的环境中，父母虽然要求严格，但一直支持你的兴趣爱好。你小时候就开始学习钢琴，也参加过一些绘画班，培养了对艺术的浓厚兴趣。 5. 爱好与兴趣： 爱好：你热爱音乐，尤其是流行歌曲，经常弹钢琴或唱歌来放松自己。你也喜欢画画，尤其是在课外时间，喜欢画一些风景或人物肖像。除此之外，你对手工艺制作也有浓厚兴趣，常常自己动手做小饰品。 体育活动：你虽然不擅长激烈的运动，但还是喜欢参加一些轻松的活动，比如羽毛球、乒乓球，或者和朋友去公园散步，享受清新的空气。 社交活动：你喜欢和朋友们一起去看电影、聊天，或者去咖啡店度过悠闲的时光。你喜欢和朋友们分享生活中的点滴，偶尔参加一些小型聚会。 6. 学业情况： 成绩：你成绩优秀，尤其在语文和英语方面表现突出，音乐和美术是你的强项。而数学和物理相对较弱，但你会加倍努力，争取提升自己。 课外活动：你是学校合唱团的一员，也加入了美术社和摄影社，喜欢通过这些活动表达自己对艺术的热爱。 7. 人际关系： 朋友：你有一群非常要好的朋友，大家性格不同，但你总是能够和每个人打成一片。你乐意分享你的快乐，并且在朋友遇到困难时总是会毫不犹豫地伸出援手。 8. 理想与未来： 理想：你的理想是成为一名音乐家或艺术家，想在钢琴上有所成就，也希望通过自己的绘画作品让世界变得更加美好。 未来规划：虽然你现在还在高中，但你已经开始考虑未来可能会去国外留学，继续深造自己感兴趣的艺术专业。 9,你平时一般用抖音,小红书这些社交软件"""
    plan_context: str = ""
    context_entries: str = ""
    extra_instructions: str = ""


class ExecutorLabRequest(BaseModel):
    title: str = Field(min_length=1)
    detail: str = Field(min_length=1)
    zone: ExecutorLabZone = ExecutorLabZone.AUTO
    capability: str = ""
    arguments: dict[str, JsonValue] = Field(default_factory=dict)
    related_event_text: str = ""
    max_turns: int = Field(default=6, ge=1, le=20)
    next_step_scheduled_for: str | None = None
    buffer_seconds: int = Field(default=30, ge=0, le=600)
    interrupt_after_turn: int | None = Field(default=None, ge=1, le=20)
    roleplay: ExecutorLabRoleplayConfig = Field(default_factory=ExecutorLabRoleplayConfig)


class ExecutorLabTurnRecord(BaseModel):
    turn_index: int
    zone: ExecutionZone
    scene: str
    result: str
    roleplay_response: str
    executor_raw_output: dict[str, JsonValue] = Field(default_factory=dict)
    next_zone: ExecutionZone | None = None
    next_scene: str | None = None
    next_result: str | None = None


class ExecutorLabResponse(BaseModel):
    resolved_zone: ExecutionZone
    resolved_capability: str | None = None
    stop_reason: str
    initial_scene: str
    initial_result: str
    final_scene: str
    final_result: str
    turns: list[ExecutorLabTurnRecord] = Field(default_factory=list)
    tool_invocations: list[ToolInvocation] = Field(default_factory=list)
    execution_trace: list[ExecutionTraceEntry] = Field(default_factory=list)
    raw_data: dict[str, JsonValue] = Field(default_factory=dict)


class ExecutorLabDefaultsResponse(BaseModel):
    roleplay: ExecutorLabRoleplayConfig
    tools: list[ToolSpec] = Field(default_factory=list)
    suggested_title: str = "看看长沙有什么好吃的"
    suggested_detail: str = "打开小搜索引擎,看看长沙有什么好吃的美食"


class ExecutorLabStreamEvent(BaseModel):
    event: str
    data: dict[str, JsonValue] = Field(default_factory=dict)


class ExecutorLabRunner:
    def __init__(
        self,
        *,
        execution_service: ExecutionService,
        memory_service: MemoryService | None,
        state: RuntimeState,
        now_provider: Callable[[], datetime] | None = None,
    ) -> None:
        self.execution_service = execution_service
        self.memory_service = memory_service
        self.state = state
        self.now_provider = now_provider

    async def run(self, request: ExecutorLabRequest) -> ExecutorLabResponse:
        previous_buffer = self.execution_service.loop_pre_replan_buffer_seconds
        self.execution_service.loop_pre_replan_buffer_seconds = request.buffer_seconds
        try:
            return await self._run_impl(request)
        finally:
            self.execution_service.loop_pre_replan_buffer_seconds = previous_buffer

    async def stream(self, request: ExecutorLabRequest) -> AsyncIterator[ExecutorLabStreamEvent]:
        yield ExecutorLabStreamEvent(
            event="started",
            data={
                "title": request.title.strip(),
                "detail": request.detail.strip(),
                "zone": request.zone.value,
                "max_turns": request.max_turns,
            },
        )

        previous_buffer = self.execution_service.loop_pre_replan_buffer_seconds
        self.execution_service.loop_pre_replan_buffer_seconds = request.buffer_seconds
        try:
            step = self._build_step(request)
            event = self._maybe_event(request.related_event_text)
            roleplay_context = self._build_roleplay_context(request)

            yield ExecutorLabStreamEvent(
                event="phase",
                data={"label": "waiting_initial_executor"},
            )
            initial_queue: asyncio.Queue[ExecutorLabStreamEvent] = asyncio.Queue()

            async def _initial_event_callback(payload: dict[str, JsonValue]) -> None:
                await initial_queue.put(ExecutorLabStreamEvent(event="executor_agent_event", data=payload))

            initial_task = asyncio.create_task(
                self._initial_turn(
                    step=step,
                    event=event,
                    request=request,
                    event_callback=_initial_event_callback,
                )
            )
            while True:
                if initial_task.done() and initial_queue.empty():
                    break
                try:
                    queued = await asyncio.wait_for(initial_queue.get(), timeout=0.1)
                except TimeoutError:
                    continue
                yield queued
            current, trace, payload, invocations, resolved_capability = await initial_task
            initial_roleplay = step.detail.strip() or step.title.strip()
            roleplay_context.add_execution_record(
                roleplay=initial_roleplay,
                scene=current.scene,
                result=current.result,
                metadata={"turn": 0, "step_id": step.step_id},
            )
            initial_turn = ExecutorLabTurnRecord(
                turn_index=0,
                zone=current.zone,
                scene=current.scene,
                result=current.result,
                roleplay_response=initial_roleplay,
                executor_raw_output=dict(payload.get("initial_executor_output", {})),
                next_zone=current.zone,
                next_scene=current.scene,
                next_result=current.result,
            )
            turns: list[ExecutorLabTurnRecord] = [initial_turn]
            yield ExecutorLabStreamEvent(
                event="initial_turn",
                data={
                    "resolved_zone": current.zone.value,
                    "resolved_capability": resolved_capability,
                    "roleplay_response": initial_turn.roleplay_response,
                    "scene": current.scene,
                    "result": current.result,
                    "executor_raw_output": initial_turn.executor_raw_output,
                },
            )
            mutable_turn = {"index": 0}
            loop_context = self._build_loop_context(request, mutable_turn)
            stop_reason: str | None = None

            for turn_index in range(request.max_turns):
                mutable_turn["index"] = turn_index
                stop_reason = self.execution_service._loop_stop_reason(loop_context)
                if stop_reason is not None:
                    break

                yield ExecutorLabStreamEvent(
                    event="phase",
                    data={"label": "waiting_roleplay", "turn_index": turn_index + 1},
                )
                roleplay_response = await self._roleplay_response(
                    context=roleplay_context,
                    current=current,
                    step=step,
                    turn_index=turn_index,
                    event=event,
                )
                if not roleplay_response:
                    stop_reason = "natural_stop"
                    break

                trace.append(
                    ExecutionTraceEntry(
                        stage=f"agent_response_{turn_index + 1}",
                        content=roleplay_response,
                    )
                )
                yield ExecutorLabStreamEvent(
                    event="roleplay_response",
                    data={"turn_index": turn_index + 1, "content": roleplay_response},
                )

                if turn_index == request.max_turns - 1:
                    record = ExecutorLabTurnRecord(
                        turn_index=turn_index + 1,
                        zone=current.zone,
                        scene=current.scene,
                        result=current.result,
                        roleplay_response=roleplay_response,
                    )
                    turns.append(record)
                    yield ExecutorLabStreamEvent(
                        event="turn_record",
                        data={"turn": record.model_dump(mode="json")},
                    )
                    stop_reason = "max_rounds"
                    break

                stop_reason = self.execution_service._loop_stop_reason(loop_context)
                if stop_reason is not None:
                    break

                yield ExecutorLabStreamEvent(
                    event="phase",
                    data={"label": "waiting_executor", "turn_index": turn_index + 1},
                )
                executor_queue: asyncio.Queue[ExecutorLabStreamEvent] = asyncio.Queue()

                async def _executor_event_callback(stream_payload: dict[str, JsonValue]) -> None:
                    await executor_queue.put(
                        ExecutorLabStreamEvent(
                            event="executor_agent_event",
                            data={"turn_index": turn_index + 1, **stream_payload},
                        )
                    )

                next_turn_task = asyncio.create_task(
                    self.execution_service._next_loop_executor_turn(
                        step=step,
                        state=self.state,
                        event=event,
                        current=current,
                        agent_response=roleplay_response,
                        turn_index=turn_index,
                        execution_trace=trace,
                        raw_data=payload,
                        tool_invocations=invocations,
                        event_callback=_executor_event_callback,
                    )
                )
                while True:
                    if next_turn_task.done() and executor_queue.empty():
                        break
                    try:
                        queued = await asyncio.wait_for(executor_queue.get(), timeout=0.1)
                    except TimeoutError:
                        continue
                    yield queued
                next_turn = await next_turn_task
                loop_outputs = payload.get("loop_executor_outputs", [])
                latest_executor_output = (
                    dict(loop_outputs[-1]) if isinstance(loop_outputs, list) and loop_outputs else {}
                )
                record = ExecutorLabTurnRecord(
                    turn_index=turn_index + 1,
                    zone=current.zone,
                    scene=current.scene,
                    result=current.result,
                    roleplay_response=roleplay_response,
                    executor_raw_output=latest_executor_output,
                    next_zone=next_turn.zone if next_turn is not None else None,
                    next_scene=next_turn.scene if next_turn is not None else None,
                    next_result=next_turn.result if next_turn is not None else None,
                )
                turns.append(record)
                yield ExecutorLabStreamEvent(
                    event="turn_record",
                    data={"turn": record.model_dump(mode="json")},
                )

                if next_turn is None:
                    stop_reason = "natural_stop"
                    break

                current = next_turn
                roleplay_context.add_execution_record(
                    roleplay=roleplay_response,
                    scene=current.scene,
                    result=current.result,
                    metadata={"turn": turn_index + 1, "step_id": step.step_id},
                )

            if stop_reason is None:
                stop_reason = "natural_stop"

            payload["loop_stop_reason"] = stop_reason
            payload["roleplay_context"] = roleplay_context.render_for_roleplay()
            trace.append(ExecutionTraceEntry(stage="loop_stop", content=stop_reason))
            response = ExecutorLabResponse(
                resolved_zone=turns[0].zone,
                resolved_capability=resolved_capability,
                stop_reason=stop_reason,
                initial_scene=turns[0].scene,
                initial_result=turns[0].result,
                final_scene=current.scene,
                final_result=current.result,
                turns=turns,
                tool_invocations=invocations,
                execution_trace=trace,
                raw_data=payload,
            )
            yield ExecutorLabStreamEvent(
                event="loop_stop",
                data={"stop_reason": stop_reason},
            )
            yield ExecutorLabStreamEvent(
                event="completed",
                data={"response": response.model_dump(mode="json")},
            )
        except Exception as exc:
            yield ExecutorLabStreamEvent(event="error", data={"detail": str(exc)})
        finally:
            self.execution_service.loop_pre_replan_buffer_seconds = previous_buffer

    async def _run_impl(
        self,
        request: ExecutorLabRequest,
        *,
        return_initial_turn: bool = False,
    ) -> ExecutorLabResponse | tuple[ExecutorLabResponse, ExecutorLabTurnRecord]:
        step = self._build_step(request)
        event = self._maybe_event(request.related_event_text)
        roleplay_context = self._build_roleplay_context(request)
        current, trace, payload, invocations, resolved_capability = await self._initial_turn(
            step=step,
            event=event,
            request=request,
        )

        initial_roleplay = step.detail.strip() or step.title.strip()
        roleplay_context.add_execution_record(
            roleplay=initial_roleplay,
            scene=current.scene,
            result=current.result,
            metadata={"turn": 0, "step_id": step.step_id},
        )
        initial_turn = ExecutorLabTurnRecord(
            turn_index=0,
            zone=current.zone,
            scene=current.scene,
            result=current.result,
            roleplay_response=initial_roleplay,
            next_zone=current.zone,
            next_scene=current.scene,
            next_result=current.result,
        )

        turns: list[ExecutorLabTurnRecord] = [initial_turn]
        mutable_turn = {"index": 0}
        loop_context = self._build_loop_context(request, mutable_turn)
        stop_reason: str | None = None

        for turn_index in range(request.max_turns):
            mutable_turn["index"] = turn_index
            stop_reason = self.execution_service._loop_stop_reason(loop_context)
            if stop_reason is not None:
                break

            roleplay_response = await self._roleplay_response(
                context=roleplay_context,
                current=current,
                step=step,
                turn_index=turn_index,
                event=event,
            )
            if not roleplay_response:
                stop_reason = "natural_stop"
                break

            trace.append(
                ExecutionTraceEntry(
                    stage=f"agent_response_{turn_index + 1}",
                    content=roleplay_response,
                )
            )

            if turn_index == request.max_turns - 1:
                turns.append(
                    ExecutorLabTurnRecord(
                        turn_index=turn_index + 1,
                        zone=current.zone,
                        scene=current.scene,
                        result=current.result,
                        roleplay_response=roleplay_response,
                    )
                )
                stop_reason = "max_rounds"
                break

            stop_reason = self.execution_service._loop_stop_reason(loop_context)
            if stop_reason is not None:
                break

            next_turn = await self.execution_service._next_loop_executor_turn(
                step=step,
                state=self.state,
                event=event,
                current=current,
                agent_response=roleplay_response,
                turn_index=turn_index,
                execution_trace=trace,
                raw_data=payload,
                tool_invocations=invocations,
            )
            record = ExecutorLabTurnRecord(
                turn_index=turn_index + 1,
                zone=current.zone,
                scene=current.scene,
                result=current.result,
                roleplay_response=roleplay_response,
                next_zone=next_turn.zone if next_turn is not None else None,
                next_scene=next_turn.scene if next_turn is not None else None,
                next_result=next_turn.result if next_turn is not None else None,
            )
            turns.append(record)

            if next_turn is None:
                stop_reason = "natural_stop"
                break

            current = next_turn
            roleplay_context.add_execution_record(
                roleplay=roleplay_response,
                scene=current.scene,
                result=current.result,
                metadata={"turn": turn_index + 1, "step_id": step.step_id},
            )

        if stop_reason is None:
            stop_reason = "natural_stop"

        payload["loop_stop_reason"] = stop_reason
        payload["roleplay_context"] = roleplay_context.render_for_roleplay()
        trace.append(ExecutionTraceEntry(stage="loop_stop", content=stop_reason))

        response = ExecutorLabResponse(
            resolved_zone=turns[0].zone,
            resolved_capability=resolved_capability,
            stop_reason=stop_reason,
            initial_scene=turns[0].scene,
            initial_result=turns[0].result,
            final_scene=current.scene,
            final_result=current.result,
            turns=turns,
            tool_invocations=invocations,
            execution_trace=trace,
            raw_data=payload,
        )
        if return_initial_turn:
            return response, initial_turn
        return response

    def _build_step(self, request: ExecutorLabRequest) -> PlanStep:
        capability = request.capability.strip()
        return PlanStep(
            title=request.title.strip(),
            detail=request.detail.strip(),
            execution_mode=ExecutionMode.HYBRID,
            zone_hint=self._requested_zone_hint(request.zone),
            capability=capability or None,
            arguments=dict(request.arguments),
        )

    async def _initial_turn(
        self,
        *,
        step: PlanStep,
        event: RuntimeEvent | None,
        request: ExecutorLabRequest,
        event_callback: Callable[[dict[str, JsonValue]], object] | None = None,
    ) -> tuple[
        ExecutionLoopTurn,
        list[ExecutionTraceEntry],
        dict[str, JsonValue],
        list[ToolInvocation],
        str | None,
    ]:
        initial_roleplay = step.detail.strip() or step.title.strip()
        executor_turn = await self.execution_service._executor_agent_turn_with_model(
            step=step,
            state=self.state,
            event=event,
            current_scene="",
            current_result="",
            agent_response=initial_roleplay,
            event_callback=event_callback,
        )
        draft, invocations, tool_results = executor_turn
        resolved_zone = ExecutionZone.REAL if invocations else ExecutionZone.NON_REAL
        trace = [
            ExecutionTraceEntry(stage="roleplay_initial", content=initial_roleplay),
            ExecutionTraceEntry(stage="scene", content=draft.scene),
            ExecutionTraceEntry(stage="result", content=draft.result),
        ]
        payload: dict[str, JsonValue] = {
            "scene": draft.scene,
            "tool_results": tool_results,
            "initial_roleplay_message": initial_roleplay,
        }
        return (
            ExecutionLoopTurn(zone=resolved_zone, scene=draft.scene, result=draft.result),
            trace,
            payload,
            invocations,
            invocations[-1].capability if invocations else None,
        )


    async def _roleplay_response(
        self,
        *,
        context: RoleplayAgentContext,
        current: ExecutionLoopTurn,
        step: PlanStep,
        turn_index: int,
        event: RuntimeEvent | None,
    ) -> str:
        roleplay_agent = self.execution_service.roleplay_agent
        if roleplay_agent is None:
            raise ExecutionRuntimeError("Executor Lab 当前没有接入 RoleplayAgent。")
        response = await roleplay_agent.respond(
            context=context,
            step=step,
            state=self.state,
            event=event,
            scene=current.scene,
            result=current.result,
            turn_index=turn_index,
        )
        return response.strip()

    def _build_roleplay_context(self, request: ExecutorLabRequest) -> RoleplayAgentContext:
        roleplay = request.roleplay
        context = RoleplayAgentContext(
            soul_md=roleplay.soul_md.strip(),
            plan_context=roleplay.plan_context.strip(),
        )
        for block in self._split_context_blocks(roleplay.context_entries):
            context.add_entry(kind="manual_context", content=block)
        if roleplay.extra_instructions.strip():
            context.add_entry(
                kind="debug_instruction",
                content=f"调试说明：{roleplay.extra_instructions.strip()}",
            )
        return context

    def _split_context_blocks(self, value: str) -> list[str]:
        blocks: list[str] = []
        current: list[str] = []
        for line in str(value or "").splitlines():
            if line.strip():
                current.append(line.rstrip())
                continue
            if current:
                blocks.append("\n".join(current).strip())
                current = []
        if current:
            blocks.append("\n".join(current).strip())
        return [block for block in blocks if block]

    def _maybe_event(self, text: str) -> RuntimeEvent | None:
        cleaned = text.strip()
        if not cleaned:
            return None
        return RuntimeEvent(
            event_type=EventType.MESSAGE_RECEIVED,
            source=EventSource.USER,
            payload={"text": cleaned},
        )

    def _build_loop_context(
        self,
        request: ExecutorLabRequest,
        turn_counter: dict[str, int],
    ) -> ExecutionLoopContext:
        next_step_at = None
        if request.next_step_scheduled_for:
            next_step_at = datetime.fromisoformat(request.next_step_scheduled_for)

        def _interrupt() -> bool:
            if request.interrupt_after_turn is None:
                return False
            return turn_counter["index"] >= request.interrupt_after_turn

        return ExecutionLoopContext(
            now_provider=self.now_provider,
            next_step_scheduled_for=next_step_at,
            should_interrupt=_interrupt,
        )

    def _requested_zone_hint(self, requested: ExecutorLabZone) -> ExecutionZone:
        if requested == ExecutorLabZone.REAL:
            return ExecutionZone.REAL
        return ExecutionZone.NON_REAL


def executor_lab_defaults(
    *,
    core_memory: CoreMemory,
    tool_specs: list[ToolSpec],
) -> ExecutorLabDefaultsResponse:
    return ExecutorLabDefaultsResponse(
        roleplay=ExecutorLabRoleplayConfig(
            name="Roleplay Agent",
            soul_md=core_memory.soul_md,
        ),
        tools=tool_specs,
    )


def empty_executor_lab_defaults(*, tool_specs: list[ToolSpec]) -> ExecutorLabDefaultsResponse:
    return ExecutorLabDefaultsResponse(
        roleplay=ExecutorLabRoleplayConfig(),
        tools=tool_specs,
    )
