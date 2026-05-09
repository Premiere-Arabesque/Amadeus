from __future__ import annotations

import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.communication.hub import CommunicationHub
from app.core.events import EventSource, EventType, RuntimeEvent
from app.core.outcomes import ActionOutcome, OutcomeStatus, ReplanDecision, ReplanKind
from app.core.state import PlanState
from app.core.types import ExecutionMode, ExecutionZone
from app.infra.env import load_project_env
from app.infra.model_client import ModelRouter, PydanticAIModelClient
from app.infra.settings import ExecutionSettings, ModelRoutingSettings
from app.main import build_orchestrator
from app.memory.models import CoreMemory, RawLogEntry
from app.memory.service import MemoryService
from app.persona.service import PersonaService
from app.prompts.store import PromptStore
from app.runtime.clock import AdjustableClock
from app.runtime.inspection import RuntimeStateSnapshot, build_runtime_snapshot
from app.runtime.orchestrator import RuntimeOrchestrator
from app.runtime.roleplay_context import RoleplayAgentContext

_PLANLAB_ROOT = Path("memory/planlab")


class PlannerLabDebugResponse(BaseModel):
    summary: RuntimeStateSnapshot
    current_plan: PlanState
    core_memory: CoreMemory
    roleplay_context: RoleplayAgentContext
    execution_granularity: str = "minute"
    day_start_memory_preview: list[str] = Field(default_factory=list)
    planning_entries: list[RawLogEntry] = Field(default_factory=list)
    replan_entries: list[RawLogEntry] = Field(default_factory=list)
    model_entries: list[RawLogEntry] = Field(default_factory=list)


class PlannerLabActionResponse(BaseModel):
    debug: PlannerLabDebugResponse


class PlannerLabDecisionResponse(BaseModel):
    decision: ReplanDecision
    debug: PlannerLabDebugResponse


class PlannerLabManualContextRequest(BaseModel):
    persona_name: str = "Amadeus"
    soul_md: str = ""
    memories: list[str] = Field(default_factory=list)


class PlannerLabClockSetRequest(BaseModel):
    at: str


class PlannerLabDayStartRequest(PlannerLabManualContextRequest):
    note: str = ""


class PlannerLabExpandBlockRequest(PlannerLabManualContextRequest):
    reason: str = ""
    force: bool = True


class PlannerLabExpandSpecificBlockRequest(PlannerLabManualContextRequest):
    block_id: str = Field(min_length=1)
    reason: str = ""


class PlannerLabReplanDecisionRequest(PlannerLabManualContextRequest):
    outcome_content: str = Field(min_length=1)
    event_text: str = ""
    execution_mode: ExecutionMode = ExecutionMode.NARRATIVE
    execution_zone: ExecutionZone = ExecutionZone.NON_REAL


class PlannerLabApplyReplanRequest(PlannerLabManualContextRequest):
    kind: ReplanKind
    reason: str = ""
    event_text: str = ""
    outcome_content: str = "Manual planner-lab replan."
    execution_mode: ExecutionMode = ExecutionMode.NARRATIVE
    execution_zone: ExecutionZone = ExecutionZone.NON_REAL


@dataclass(slots=True)
class PlannerLabSession:
    workspace_root: Path
    clock: AdjustableClock
    memory_service: MemoryService
    persona_service: PersonaService
    orchestrator: RuntimeOrchestrator


def create_app() -> FastAPI:
    load_project_env()
    routing_settings = ModelRoutingSettings.from_env()
    execution_settings = ExecutionSettings.from_env()
    model_router = ModelRouter(routing_settings)
    model_client = PydanticAIModelClient()
    prompt_store = PromptStore()
    front_root = Path(__file__).resolve().parent / "front"

    app = FastAPI(title="Amadeus Planner Lab", version="0.1.0")
    app.mount("/assets", StaticFiles(directory=front_root / "assets"), name="assets")

    def _build_session(*, reset_workspace: bool) -> PlannerLabSession:
        if reset_workspace:
            _reset_workspace(_PLANLAB_ROOT)
        else:
            _PLANLAB_ROOT.mkdir(parents=True, exist_ok=True)

        memory_service = MemoryService(
            raw_log_path=_PLANLAB_ROOT / "raw_log",
            snapshot_path=_PLANLAB_ROOT / "snapshots.jsonl",
            active_memory_path=_PLANLAB_ROOT / "active_memory.jsonl",
            core_memory_path=_PLANLAB_ROOT / "core_memory.json",
            roleplay_context_path=_PLANLAB_ROOT / "roleplay_context.json",
            archive_memory_path=_PLANLAB_ROOT / "archive_memory.jsonl",
            model_client=model_client,
            model_router=model_router,
            prompt_store=prompt_store,
        )
        persona_service = PersonaService(
            soul_path=_PLANLAB_ROOT / "soul.md",
            model_client=model_client,
            model_router=model_router,
            prompt_store=prompt_store,
        )
        binder = getattr(model_client, "bind_trace_sink", None)
        if callable(binder):
            binder(memory_service.record_model_trace)

        clock = AdjustableClock(tick_real_time=False)
        orchestrator = build_orchestrator(
            communication_hub=CommunicationHub(),
            memory_service=memory_service,
            clock=clock,
            model_client=model_client,
            model_router=model_router,
            execution_settings=execution_settings,
            prompt_store=prompt_store,
        )
        return PlannerLabSession(
            workspace_root=_PLANLAB_ROOT,
            clock=clock,
            memory_service=memory_service,
            persona_service=persona_service,
            orchestrator=orchestrator,
        )

    app.state.session = _build_session(reset_workspace=True)

    def _current_session() -> PlannerLabSession:
        return app.state.session

    def _replace_session(*, reset_workspace: bool) -> PlannerLabSession:
        session = _build_session(reset_workspace=reset_workspace)
        app.state.session = session
        return session

    def _debug_payload(session: PlannerLabSession, *, limit: int) -> PlannerLabDebugResponse:
        return PlannerLabDebugResponse(
            summary=build_runtime_snapshot(
                state=session.orchestrator.state,
                orchestrator=session.orchestrator,
                mcp_provider=None,
            ),
            current_plan=session.orchestrator.state.plan,
            core_memory=session.memory_service.core_memory,
            roleplay_context=session.memory_service.get_persisted_roleplay_agent_context(),
            execution_granularity=getattr(
                getattr(session.orchestrator.services.planning, "execution_granularity", None),
                "value",
                "minute",
            ),
            day_start_memory_preview=session.memory_service.day_start_memory_context(
                now=session.orchestrator.now(),
                limit=6,
            ),
            planning_entries=_recent_raw_entries_by_kind(
                session.memory_service,
                kind="planning",
                limit=limit,
            ),
            replan_entries=_recent_raw_entries_by_kind(
                session.memory_service,
                kind="replan",
                limit=limit,
            ),
            model_entries=_recent_raw_entries_by_kind(
                session.memory_service,
                kind="model_io",
                limit=limit,
            ),
        )

    @app.get("/", response_class=HTMLResponse)
    async def home() -> HTMLResponse:
        return HTMLResponse(
            (front_root / "pages" / "planner-lab-standalone.html").read_text(
                encoding="utf-8"
            )
        )

    @app.get("/planner-lab", response_class=HTMLResponse)
    async def planner_lab_page() -> HTMLResponse:
        return HTMLResponse(
            (front_root / "pages" / "planner-lab-standalone.html").read_text(
                encoding="utf-8"
            )
        )

    @app.get("/health")
    async def health() -> dict[str, object]:
        session = _current_session()
        return {
            "status": "ok",
            "workspace_root": str(session.workspace_root),
            "current_time": session.orchestrator.now().isoformat(),
            "runtime_status": build_runtime_snapshot(
                state=session.orchestrator.state,
                orchestrator=session.orchestrator,
                mcp_provider=None,
            ).runtime_status,
        }

    @app.post("/api/planner-lab/reset", response_model=PlannerLabActionResponse)
    async def reset_planner_lab() -> PlannerLabActionResponse:
        session = _replace_session(reset_workspace=True)
        return PlannerLabActionResponse(debug=_debug_payload(session, limit=20))

    @app.get("/api/planner-lab/debug", response_model=PlannerLabDebugResponse)
    async def planner_lab_debug(
        limit: int = Query(default=20, ge=1, le=100),
    ) -> PlannerLabDebugResponse:
        return _debug_payload(_current_session(), limit=limit)

    @app.post("/api/planner-lab/clock/set", response_model=PlannerLabActionResponse)
    async def planner_lab_set_clock(
        request: PlannerLabClockSetRequest,
    ) -> PlannerLabActionResponse:
        session = _current_session()
        try:
            target = datetime.fromisoformat(request.at)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="Invalid ISO 8601 datetime.") from exc
        session.orchestrator.set_time(target)
        sync_plan = getattr(session.orchestrator.services.planning, "sync_plan_to_time", None)
        if callable(sync_plan):
            refreshed = await sync_plan(
                session.orchestrator.state,
                now=target,
            )
            session.orchestrator.state.plan = refreshed
            session.memory_service.update_plan_context(
                day_blocks=refreshed.day_blocks,
                plan_date=refreshed.plan_date,
            )
            await session.memory_service.save_snapshot(session.orchestrator.state)
        return PlannerLabActionResponse(debug=_debug_payload(session, limit=20))

    @app.post("/api/planner-lab/day-start", response_model=PlannerLabActionResponse)
    async def planner_lab_day_start(
        request: PlannerLabDayStartRequest,
    ) -> PlannerLabActionResponse:
        session = _current_session()
        _apply_manual_context(session, request)
        now = session.orchestrator.now()
        payload = {"note": request.note.strip()} if request.note.strip() else {}
        event = RuntimeEvent(
            event_type=EventType.DAY_START,
            source=EventSource.RUNTIME,
            created_at=now.isoformat(),
            payload=payload,
        )
        session.memory_service.record_runtime_event(event)
        plan = await session.orchestrator.services.planning.plan_next_window(
            session.orchestrator.state,
            event,
            now=now,
        )
        session.orchestrator.state.plan = plan
        session.memory_service.update_plan_context(
            day_blocks=plan.day_blocks,
            plan_date=plan.plan_date,
        )
        await session.memory_service.save_snapshot(session.orchestrator.state)
        return PlannerLabActionResponse(debug=_debug_payload(session, limit=20))

    @app.post("/api/planner-lab/expand-ready-block", response_model=PlannerLabActionResponse)
    async def planner_lab_expand_ready_block(
        request: PlannerLabExpandBlockRequest,
    ) -> PlannerLabActionResponse:
        session = _current_session()
        _apply_manual_context(session, request)
        now = session.orchestrator.now()

        if not session.orchestrator.state.plan.day_blocks:
            raise HTTPException(
                status_code=409,
                detail="No day plan exists yet. Run day_start first.",
            )

        state_for_expand = session.orchestrator.state.model_copy(deep=True)
        state_for_expand.plan.minute_steps = []

        event_payload: dict[str, object] = {}
        if request.reason.strip():
            event_payload["reason"] = request.reason.strip()
        event = RuntimeEvent(
            event_type=EventType.PLAN_REFRESH_REQUESTED,
            source=EventSource.RUNTIME,
            created_at=now.isoformat(),
            payload=event_payload,
        )
        expanded = await session.orchestrator.services.planning.expand_ready_block(
            state_for_expand,
            now=now,
            trigger_event=event,
            force=request.force,
            reason=request.reason.strip(),
        )
        if expanded is None:
            raise HTTPException(
                status_code=409,
                detail="No ready time block could be expanded at the current virtual time.",
            )
        session.orchestrator.state.plan = expanded
        session.memory_service.update_plan_context(
            day_blocks=expanded.day_blocks,
            plan_date=expanded.plan_date,
        )
        await session.memory_service.save_snapshot(session.orchestrator.state)
        return PlannerLabActionResponse(debug=_debug_payload(session, limit=20))

    @app.post("/api/planner-lab/expand-block", response_model=PlannerLabActionResponse)
    async def planner_lab_expand_specific_block(
        request: PlannerLabExpandSpecificBlockRequest,
    ) -> PlannerLabActionResponse:
        session = _current_session()
        _apply_manual_context(session, request)
        now = session.orchestrator.now()

        if not session.orchestrator.state.plan.day_blocks:
            raise HTTPException(
                status_code=409,
                detail="No day plan exists yet. Run day_start first.",
            )

        event_payload: dict[str, object] = {"block_id": request.block_id}
        if request.reason.strip():
            event_payload["reason"] = request.reason.strip()
        event = RuntimeEvent(
            event_type=EventType.PLAN_REFRESH_REQUESTED,
            source=EventSource.RUNTIME,
            created_at=now.isoformat(),
            payload=event_payload,
        )
        expanded = await session.orchestrator.services.planning.expand_specific_block(
            session.orchestrator.state,
            block_id=request.block_id,
            now=now,
            trigger_event=event,
            reason=request.reason.strip(),
        )
        if expanded is None:
            raise HTTPException(
                status_code=404,
                detail=f"Could not expand block `{request.block_id}`.",
            )
        session.orchestrator.state.plan = expanded
        session.memory_service.update_plan_context(
            day_blocks=expanded.day_blocks,
            plan_date=expanded.plan_date,
        )
        await session.memory_service.save_snapshot(session.orchestrator.state)
        return PlannerLabActionResponse(debug=_debug_payload(session, limit=20))

    @app.post("/api/planner-lab/replan/decide", response_model=PlannerLabDecisionResponse)
    async def planner_lab_replan_decide(
        request: PlannerLabReplanDecisionRequest,
    ) -> PlannerLabDecisionResponse:
        session = _current_session()
        _apply_manual_context(session, request)
        now = session.orchestrator.now()
        event_payload: dict[str, object] = {}
        if request.event_text.strip():
            event_payload["text"] = request.event_text.strip()
        event = RuntimeEvent(
            event_type=EventType.ACTION_COMPLETED,
            source=EventSource.RUNTIME,
            created_at=now.isoformat(),
            payload=event_payload,
        )
        outcome = ActionOutcome(
            action_id=session.orchestrator.state.current_action_id or "planner_lab_action",
            status=OutcomeStatus.SUCCESS,
            mode=request.execution_mode,
            source=request.execution_zone,
            content=request.outcome_content,
        )
        decision = await session.orchestrator.services.replan.decide(
            now=now,
            state=session.orchestrator.state,
            event=event,
            outcome=outcome,
        )
        session.memory_service.record_replan_decision(
            decision,
            event=event,
            outcome=outcome,
        )
        return PlannerLabDecisionResponse(
            decision=decision,
            debug=_debug_payload(session, limit=20),
        )

    @app.post("/api/planner-lab/replan/apply", response_model=PlannerLabActionResponse)
    async def planner_lab_replan_apply(
        request: PlannerLabApplyReplanRequest,
    ) -> PlannerLabActionResponse:
        session = _current_session()
        _apply_manual_context(session, request)
        now = session.orchestrator.now()
        event_payload: dict[str, object] = {"replan_kind": request.kind.value}
        if request.reason.strip():
            event_payload["reason"] = request.reason.strip()
        if request.event_text.strip():
            event_payload["text"] = request.event_text.strip()
        event = RuntimeEvent(
            event_type=EventType.ACTION_COMPLETED,
            source=EventSource.RUNTIME,
            created_at=now.isoformat(),
            payload=event_payload,
        )
        outcome = ActionOutcome(
            action_id=session.orchestrator.state.current_action_id or "planner_lab_action",
            status=OutcomeStatus.SUCCESS,
            mode=request.execution_mode,
            source=request.execution_zone,
            content=request.outcome_content,
        )
        plan = await session.orchestrator.services.planning.replan_after_completion(
            session.orchestrator.state,
            now=now,
            kind=request.kind,
            reason=request.reason.strip(),
            event=event,
            outcome=outcome,
        )
        session.orchestrator.state.plan = plan
        session.memory_service.update_plan_context(
            day_blocks=plan.day_blocks,
            plan_date=plan.plan_date,
        )
        session.memory_service.record_replan_decision(
            ReplanDecision(
                kind=request.kind,
                reason=request.reason.strip(),
                source="manual_apply",
            ),
            event=event,
            outcome=outcome,
        )
        await session.memory_service.save_snapshot(session.orchestrator.state)
        return PlannerLabActionResponse(debug=_debug_payload(session, limit=20))

    return app


def _reset_workspace(root: Path) -> None:
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True, exist_ok=True)


def _recent_raw_entries_by_kind(
    memory_service: MemoryService,
    *,
    kind: str,
    limit: int,
) -> list[RawLogEntry]:
    entries = getattr(memory_service, "raw_entries", None)
    if not isinstance(entries, list):
        return []
    matches: list[RawLogEntry] = []
    for entry in reversed(entries):
        if not isinstance(entry, RawLogEntry) or entry.kind != kind:
            continue
        matches.append(entry)
        if len(matches) >= limit:
            break
    return matches


def _apply_manual_context(
    session: PlannerLabSession,
    request: PlannerLabManualContextRequest,
) -> None:
    persona_name = " ".join(request.persona_name.split()).strip() or "Amadeus"
    session.orchestrator.state.persona_name = persona_name

    session.memory_service.update_persona_context(
        soul_md=request.soul_md.strip(),
    )
    cleaned_memories = [
        str(item).strip()
        for item in request.memories
        if str(item).strip()
    ]
    setter = getattr(session.memory_service, "set_manual_context_memories", None)
    if callable(setter):
        setter(cleaned_memories)


app = create_app()
