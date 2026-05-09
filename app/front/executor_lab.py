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
from app.runtime.contact_book import ContactBook, ContactEntry
from app.runtime.execution import (
    LOOP_STOP_MAX_ROUNDS,
    LOOP_STOP_NATURAL,
    LOOP_STOP_PROACTIVE_INTERACTION,
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


class ExecutorLabRoleplayConfig(BaseModel):
    name: str = "花梨"
    soul_md: str = ""
    plan_context: str = ""
    context_entries: str = ""
    extra_instructions: str = ""
    registered_contacts: str = "用户 | api | default-user"


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
    executor_events: list[dict[str, JsonValue]] = Field(default_factory=list)
    next_zone: ExecutionZone | None = None
    next_scene: str | None = None
    next_result: str | None = None
    stop_reason: str | None = None
    handoff_payload: dict[str, JsonValue] = Field(default_factory=dict)


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
    suggested_title: str = "搜搜长沙有什么好吃的"
    suggested_detail: str = "打开搜索引擎，搜搜长沙有什么好吃的"


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
        contact_book: ContactBook | None = None,
        now_provider: Callable[[], datetime] | None = None,
    ) -> None:
        self.execution_service = execution_service
        self.memory_service = memory_service
        self.state = state
        self.contact_book = contact_book or ContactBook()
        self.now_provider = now_provider

    async def run(self, request: ExecutorLabRequest) -> ExecutorLabResponse:
        self._seed_contact_book(request)
        previous_buffer = self.execution_service.loop_pre_replan_buffer_seconds
        self.execution_service.loop_pre_replan_buffer_seconds = request.buffer_seconds
        try:
            return await self._run_impl(request)
        finally:
            self.execution_service.loop_pre_replan_buffer_seconds = previous_buffer

    async def stream(self, request: ExecutorLabRequest) -> AsyncIterator[ExecutorLabStreamEvent]:
        self._seed_contact_book(request)
        yield ExecutorLabStreamEvent(
            event="started",
            data={
                "title": request.title.strip(),
                "detail": request.detail.strip(),
                "zone": request.zone.value,
                "max_turns": request.max_turns,
                "registered_contacts": [contact.model_dump(mode="json") for contact in self.contact_book.list_contacts()],
            },
        )

        previous_buffer = self.execution_service.loop_pre_replan_buffer_seconds
        self.execution_service.loop_pre_replan_buffer_seconds = request.buffer_seconds
        try:
            step = self._build_step(request)
            event = self._maybe_event(request.related_event_text)
            roleplay_context = self._build_roleplay_context(request)

            yield ExecutorLabStreamEvent(event="phase", data={"label": "waiting_initial_executor"})
            initial_queue: asyncio.Queue[ExecutorLabStreamEvent] = asyncio.Queue()

            async def initial_event_callback(payload: dict[str, JsonValue]) -> None:
                await initial_queue.put(ExecutorLabStreamEvent(event="executor_agent_event", data=payload))

            initial_task = asyncio.create_task(
                self._initial_turn(
                    step=step,
                    event=event,
                    event_callback=initial_event_callback,
                )
            )
            while True:
                if initial_task.done() and initial_queue.empty():
                    break
                try:
                    queued = await asyncio.wait_for(initial_queue.get(), timeout=0.1)
                except asyncio.TimeoutError:
                    continue
                yield queued

            current, trace, payload, invocations, resolved_capability = await initial_task
            initial_stop_reason = str(payload.get("loop_stop_reason", "")).strip() or None
            initial_handoff_payload = (
                dict(payload["proactive_interaction"])
                if isinstance(payload.get("proactive_interaction"), dict)
                else None
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
                stop_reason=initial_stop_reason,
                handoff_payload=initial_handoff_payload,
                executor_raw_output=dict(payload.get("initial_executor_output", {})),
                executor_events=self._initial_executor_events(payload),
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
                    "stop_reason": initial_stop_reason,
                    "proactive_interaction": initial_handoff_payload,
                    "executor_raw_output": initial_turn.executor_raw_output,
                    "executor_events": initial_turn.executor_events,
                },
            )

            mutable_turn = {"index": 0}
            loop_context = self._build_loop_context(request, mutable_turn)
            stop_reason: str | None = initial_stop_reason

            if (
                stop_reason == LOOP_STOP_PROACTIVE_INTERACTION
                and initial_handoff_payload is not None
            ):
                yield ExecutorLabStreamEvent(
                    event="proactive_interaction",
                    data={"turn_index": 0, **initial_handoff_payload},
                )

            for turn_index in range(request.max_turns) if stop_reason is None else range(0):
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
                    stop_reason = LOOP_STOP_NATURAL
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
                    stop_reason = LOOP_STOP_MAX_ROUNDS
                    record = ExecutorLabTurnRecord(
                        turn_index=turn_index + 1,
                        zone=current.zone,
                        scene=current.scene,
                        result=current.result,
                        roleplay_response=roleplay_response,
                        stop_reason=stop_reason,
                        executor_events=self._latest_executor_events(payload),
                    )
                    turns.append(record)
                    yield ExecutorLabStreamEvent(
                        event="turn_record",
                        data={"turn": record.model_dump(mode="json")},
                    )
                    break

                stop_reason = self.execution_service._loop_stop_reason(loop_context)
                if stop_reason is not None:
                    break

                yield ExecutorLabStreamEvent(
                    event="phase",
                    data={"label": "waiting_executor", "turn_index": turn_index + 1},
                )
                executor_queue: asyncio.Queue[ExecutorLabStreamEvent] = asyncio.Queue()

                async def executor_event_callback(stream_payload: dict[str, JsonValue]) -> None:
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
                        event_callback=executor_event_callback,
                    )
                )
                while True:
                    if next_turn_task.done() and executor_queue.empty():
                        break
                    try:
                        queued = await asyncio.wait_for(executor_queue.get(), timeout=0.1)
                    except asyncio.TimeoutError:
                        continue
                    yield queued

                next_turn_result = await next_turn_task
                latest_executor_output = self._latest_executor_output(payload)
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
                    record = ExecutorLabTurnRecord(
                        turn_index=turn_index + 1,
                        zone=current.zone,
                        scene=current.scene,
                        result=current.result,
                        roleplay_response=roleplay_response,
                        stop_reason=stop_reason,
                        handoff_payload=proactive_payload,
                        executor_raw_output=latest_executor_output,
                        executor_events=self._latest_executor_events(payload),
                    )
                    turns.append(record)
                    yield ExecutorLabStreamEvent(
                        event="proactive_interaction",
                        data={"turn_index": turn_index + 1, **proactive_payload},
                    )
                    yield ExecutorLabStreamEvent(
                        event="turn_record",
                        data={"turn": record.model_dump(mode="json")},
                    )
                    break
                next_turn = next_turn_result.next_turn
                record = ExecutorLabTurnRecord(
                    turn_index=turn_index + 1,
                    zone=current.zone,
                    scene=current.scene,
                    result=current.result,
                    roleplay_response=roleplay_response,
                    executor_raw_output=latest_executor_output,
                    executor_events=self._latest_executor_events(payload),
                    next_zone=next_turn.zone if next_turn is not None else None,
                    next_scene=next_turn.scene if next_turn is not None else None,
                    next_result=next_turn.result if next_turn is not None else None,
                    stop_reason=LOOP_STOP_NATURAL if next_turn is None else None,
                )
                turns.append(record)
                yield ExecutorLabStreamEvent(
                    event="turn_record",
                    data={"turn": record.model_dump(mode="json")},
                )

                if next_turn is None:
                    stop_reason = LOOP_STOP_NATURAL
                    break

                current = next_turn
                roleplay_context.add_execution_record(
                    roleplay=roleplay_response,
                    scene=current.scene,
                    result=current.result,
                    metadata={"turn": turn_index + 1, "step_id": step.step_id},
                )

            if stop_reason is None:
                stop_reason = LOOP_STOP_NATURAL

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
                data={
                    "stop_reason": stop_reason,
                    "proactive_interaction": payload.get("proactive_interaction"),
                },
            )
            yield ExecutorLabStreamEvent(
                event="completed",
                data={"response": response.model_dump(mode="json")},
            )
        except Exception as exc:
            yield ExecutorLabStreamEvent(event="error", data={"detail": str(exc)})
        finally:
            self.execution_service.loop_pre_replan_buffer_seconds = previous_buffer

    async def _run_impl(self, request: ExecutorLabRequest) -> ExecutorLabResponse:
        step = self._build_step(request)
        event = self._maybe_event(request.related_event_text)
        roleplay_context = self._build_roleplay_context(request)

        current, trace, payload, invocations, resolved_capability = await self._initial_turn(
            step=step,
            event=event,
        )
        initial_stop_reason = str(payload.get("loop_stop_reason", "")).strip() or None
        initial_handoff_payload = (
            dict(payload["proactive_interaction"])
            if isinstance(payload.get("proactive_interaction"), dict)
            else None
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
            stop_reason=initial_stop_reason,
            handoff_payload=initial_handoff_payload,
            executor_raw_output=dict(payload.get("initial_executor_output", {})),
            executor_events=self._initial_executor_events(payload),
            next_zone=current.zone,
            next_scene=current.scene,
            next_result=current.result,
        )

        turns: list[ExecutorLabTurnRecord] = [initial_turn]
        mutable_turn = {"index": 0}
        loop_context = self._build_loop_context(request, mutable_turn)
        stop_reason: str | None = initial_stop_reason

        for turn_index in range(request.max_turns) if stop_reason is None else range(0):
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
                stop_reason = LOOP_STOP_NATURAL
                break

            trace.append(
                ExecutionTraceEntry(
                    stage=f"agent_response_{turn_index + 1}",
                    content=roleplay_response,
                )
            )

            if turn_index == request.max_turns - 1:
                stop_reason = LOOP_STOP_MAX_ROUNDS
                turns.append(
                    ExecutorLabTurnRecord(
                        turn_index=turn_index + 1,
                        zone=current.zone,
                        scene=current.scene,
                        result=current.result,
                        roleplay_response=roleplay_response,
                        stop_reason=stop_reason,
                        executor_events=self._latest_executor_events(payload),
                    )
                )
                break

            stop_reason = self.execution_service._loop_stop_reason(loop_context)
            if stop_reason is not None:
                break

            next_turn_result = await self.execution_service._next_loop_executor_turn(
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
            if next_turn_result.proactive_payload is not None:
                payload["proactive_interaction"] = next_turn_result.proactive_payload
                trace.append(
                    ExecutionTraceEntry(
                        stage=f"proactive_interaction_{turn_index + 1}",
                        content=(
                            f"{next_turn_result.proactive_payload['name']}: "
                            f"{next_turn_result.proactive_payload['message_content']}"
                        ),
                    )
                )
                stop_reason = LOOP_STOP_PROACTIVE_INTERACTION
                turns.append(
                    ExecutorLabTurnRecord(
                        turn_index=turn_index + 1,
                        zone=current.zone,
                        scene=current.scene,
                        result=current.result,
                        roleplay_response=roleplay_response,
                        stop_reason=stop_reason,
                        handoff_payload=next_turn_result.proactive_payload,
                        executor_raw_output=self._latest_executor_output(payload),
                        executor_events=self._latest_executor_events(payload),
                    )
                )
                break

            next_turn = next_turn_result.next_turn
            turns.append(
                ExecutorLabTurnRecord(
                    turn_index=turn_index + 1,
                    zone=current.zone,
                    scene=current.scene,
                    result=current.result,
                    roleplay_response=roleplay_response,
                    executor_raw_output=self._latest_executor_output(payload),
                    executor_events=self._latest_executor_events(payload),
                    next_zone=next_turn.zone if next_turn is not None else None,
                    next_scene=next_turn.scene if next_turn is not None else None,
                    next_result=next_turn.result if next_turn is not None else None,
                    stop_reason=LOOP_STOP_NATURAL if next_turn is None else None,
                )
            )

            if next_turn is None:
                stop_reason = LOOP_STOP_NATURAL
                break

            current = next_turn
            roleplay_context.add_execution_record(
                roleplay=roleplay_response,
                scene=current.scene,
                result=current.result,
                metadata={"turn": turn_index + 1, "step_id": step.step_id},
            )

        if stop_reason is None:
            stop_reason = LOOP_STOP_NATURAL

        payload["loop_stop_reason"] = stop_reason
        payload["roleplay_context"] = roleplay_context.render_for_roleplay()
        trace.append(ExecutionTraceEntry(stage="loop_stop", content=stop_reason))
        return ExecutorLabResponse(
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
        event_callback: Callable[[dict[str, JsonValue]], object] | None = None,
    ) -> tuple[
        ExecutionLoopTurn,
        list[ExecutionTraceEntry],
        dict[str, JsonValue],
        list[ToolInvocation],
        str | None,
    ]:
        initial_roleplay = step.detail.strip() or step.title.strip()
        draft, invocations, tool_results, executor_events = await self.execution_service._executor_agent_turn_with_model(
            step=step,
            state=self.state,
            event=event,
            current_scene="",
            current_result="",
            agent_response=initial_roleplay,
            event_callback=event_callback,
        )
        resolved_zone = ExecutionZone.REAL if invocations else ExecutionZone.NON_REAL
        trace = [ExecutionTraceEntry(stage="roleplay_initial", content=initial_roleplay)]
        payload: dict[str, JsonValue] = {
            "tool_results": tool_results,
            "initial_executor_events": executor_events,
            "initial_executor_output": draft.model_dump(mode="json"),
            "initial_roleplay_message": initial_roleplay,
            "executor_history": [
                self.execution_service._build_executor_history_item(
                    roleplay_message=initial_roleplay,
                    draft=draft,
                    tool_invocations=invocations,
                    executor_events=executor_events,
                )
            ],
        }
        current = ExecutionLoopTurn(zone=resolved_zone, scene="", result="")
        if draft.kind == "scene_result":
            trace.extend(
                [
                    ExecutionTraceEntry(stage="scene", content=draft.scene),
                    ExecutionTraceEntry(stage="result", content=draft.result),
                ]
            )
            payload["scene"] = draft.scene
            current = ExecutionLoopTurn(zone=resolved_zone, scene=draft.scene, result=draft.result)
        elif draft.kind == "proactive_contact":
            proactive_payload = self.execution_service._proactive_payload_from_draft(draft)
            if proactive_payload is not None:
                payload["proactive_interaction"] = proactive_payload
                payload["loop_stop_reason"] = LOOP_STOP_PROACTIVE_INTERACTION
                trace.append(
                    ExecutionTraceEntry(
                        stage="proactive_interaction",
                        content=f"{proactive_payload['name']}: {proactive_payload['message_content']}",
                    )
                )
        else:
            payload["loop_stop_reason"] = LOOP_STOP_NATURAL
            trace.append(
                ExecutionTraceEntry(
                    stage="stop_reason",
                    content=self.execution_service._stop_reason_from_draft(draft),
                )
            )
        return (
            current,
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
            context_date=self._current_time().date().isoformat(),
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

    def _seed_contact_book(self, request: ExecutorLabRequest) -> None:
        self.contact_book.replace_contacts(self._parse_contact_lines(request.roleplay.registered_contacts))

    def _parse_contact_lines(self, value: str) -> list[ContactEntry]:
        contacts: list[ContactEntry] = []
        for raw_line in str(value or "").splitlines():
            line = raw_line.strip()
            if not line:
                continue
            parts = [part.strip() for part in line.split("|")]
            name = parts[0] if parts else ""
            if not name:
                continue
            channel = parts[1] if len(parts) > 1 and parts[1] else "api"
            recipient_id = parts[2] if len(parts) > 2 and parts[2] else "default-user"
            contacts.append(
                ContactEntry(
                    name=name,
                    channel=channel,
                    recipient_id=recipient_id,
                    kind="user",
                    enabled=True,
                )
            )
        return contacts

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

        def should_interrupt() -> bool:
            if request.interrupt_after_turn is None:
                return False
            return turn_counter["index"] >= request.interrupt_after_turn

        return ExecutionLoopContext(
            now_provider=self.now_provider,
            next_step_scheduled_for=next_step_at,
            should_interrupt=should_interrupt,
        )

    def _requested_zone_hint(self, requested: ExecutorLabZone) -> ExecutionZone:
        if requested == ExecutorLabZone.REAL:
            return ExecutionZone.REAL
        return ExecutionZone.NON_REAL

    def _latest_executor_output(self, payload: dict[str, JsonValue]) -> dict[str, JsonValue]:
        loop_outputs = payload.get("loop_executor_outputs", [])
        if isinstance(loop_outputs, list) and loop_outputs:
            last = loop_outputs[-1]
            if isinstance(last, dict):
                return dict(last)
        return {}

    def _initial_executor_events(self, payload: dict[str, JsonValue]) -> list[dict[str, JsonValue]]:
        raw_events = payload.get("initial_executor_events", [])
        if not isinstance(raw_events, list):
            return []
        return [dict(item) for item in raw_events if isinstance(item, dict)]

    def _latest_executor_events(self, payload: dict[str, JsonValue]) -> list[dict[str, JsonValue]]:
        raw_events = payload.get("loop_executor_events", [])
        if isinstance(raw_events, list) and raw_events:
            last = raw_events[-1]
            if isinstance(last, list):
                return [dict(item) for item in last if isinstance(item, dict)]
        return []

    def _current_time(self) -> datetime:
        if callable(self.now_provider):
            return self.now_provider()
        return datetime.now()


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
