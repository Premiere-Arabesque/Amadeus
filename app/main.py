from __future__ import annotations

from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field

from app.communication.hub import CommunicationHub
from app.communication.qq import QQAdapter, QQBotSettings
from app.core.events import EventSource, EventType, RuntimeEvent
from app.core.outcomes import ActionOutcome
from app.core.state import RuntimeState
from app.infra.env import load_project_env
from app.infra.model_client import ModelClient, ModelRouter, PydanticAIModelClient
from app.infra.settings import ModelRoutingSettings
from app.mcp.builtins import register_builtin_capabilities
from app.mcp.compat import MCPCompatLayer
from app.mcp.registry import CapabilityRegistry
from app.memory.models import ActiveMemoryEntry, ArchiveMemoryEntry, CoreMemory, RawLogEntry
from app.memory.service import MemoryService
from app.persona.models import PersonaProfile
from app.persona.service import PersonaService
from app.runtime.emotion import EmotionService
from app.runtime.execution import ExecutionService
from app.runtime.interaction import InteractionPolicy
from app.runtime.orchestrator import OrchestratorServices, RuntimeOrchestrator
from app.runtime.planning import PlanningService
from app.runtime.replan import ReplanService


class InboundMessageRequest(BaseModel):
    user_id: str = "default-user"
    channel: str = "api"
    text: str = Field(min_length=1)


class PersonaBootstrapRequest(BaseModel):
    name: str = "Amadeus"
    seed_text: str = Field(min_length=1)


class RuntimeStateSummary(BaseModel):
    emotion_summary: str
    plan_summary: str
    current_action_id: str | None = None
    next_wake_at: str | None = None
    next_step_id: str | None = None
    next_step_scheduled_for: str | None = None
    pending_event_ids: list[str] = Field(default_factory=list)


class MessageLoopResponse(BaseModel):
    event_id: str
    outcome: ActionOutcome | None = None
    outbound_messages: list[dict[str, str]] = Field(default_factory=list)
    state: RuntimeStateSummary


class RuntimeStateInspectionResponse(BaseModel):
    state: RuntimeState
    latest_snapshot_id: str | None = None
    latest_snapshot_at: str | None = None
    next_wake_at: str | None = None
    next_step_id: str | None = None
    next_step_scheduled_for: str | None = None


class MemoryInspectionResponse(BaseModel):
    core_memory: CoreMemory
    active_entries: list[ActiveMemoryEntry] = Field(default_factory=list)
    archive_entries: list[ArchiveMemoryEntry] = Field(default_factory=list)
    raw_entries: list[RawLogEntry] = Field(default_factory=list)
    latest_snapshot_id: str | None = None
    latest_snapshot_at: str | None = None


class MemorySearchResponse(BaseModel):
    query: str
    active_hits: list[ActiveMemoryEntry] = Field(default_factory=list)
    archive_hits: list[ArchiveMemoryEntry] = Field(default_factory=list)


class PersonaInspectionResponse(BaseModel):
    profile: PersonaProfile
    core_memory: CoreMemory


def build_orchestrator(
    communication_hub: CommunicationHub,
    memory_service: MemoryService,
    initial_state: RuntimeState | None = None,
    capability_registry: CapabilityRegistry | None = None,
    read_url_http_client: httpx.AsyncClient | None = None,
    search_web_http_client: httpx.AsyncClient | None = None,
    model_client: ModelClient | None = None,
    model_router: ModelRouter | None = None,
) -> RuntimeOrchestrator:
    registry = capability_registry or CapabilityRegistry()
    register_builtin_capabilities(
        registry,
        read_url_http_client=read_url_http_client,
        search_web_http_client=search_web_http_client,
    )
    model_client = model_client or PydanticAIModelClient()
    return RuntimeOrchestrator(
        services=OrchestratorServices(
            planning=PlanningService(
                model_client=model_client,
                memory_service=memory_service,
            ),
            execution=ExecutionService(
                mcp_layer=MCPCompatLayer(registry=registry),
                model_client=model_client,
                model_router=model_router,
                memory_service=memory_service,
            ),
            emotion=EmotionService(),
            replan=ReplanService(),
            interaction=InteractionPolicy(
                model_client=model_client,
                model_router=model_router,
            ),
            memory=memory_service,
            communication=communication_hub,
        ),
        initial_state=initial_state,
    )


def create_app(
    *,
    communication_hub: CommunicationHub | None = None,
    memory_service: MemoryService | None = None,
    persona_service: PersonaService | None = None,
    routing_settings: ModelRoutingSettings | None = None,
    qq_settings: QQBotSettings | None = None,
    qq_http_client: httpx.AsyncClient | None = None,
    capability_registry: CapabilityRegistry | None = None,
    read_url_http_client: httpx.AsyncClient | None = None,
    search_web_http_client: httpx.AsyncClient | None = None,
    model_client: ModelClient | None = None,
) -> FastAPI:
    load_project_env()
    routing_settings = routing_settings or ModelRoutingSettings.from_env()
    model_router = ModelRouter(settings=routing_settings)
    model_client = model_client or PydanticAIModelClient()
    communication_hub = communication_hub or CommunicationHub()
    memory_service = memory_service or MemoryService(
        model_client=model_client,
        model_router=model_router,
    )
    persona_service = persona_service or PersonaService(
        model_client=model_client,
        model_router=model_router,
    )
    qq_settings = qq_settings or QQBotSettings.from_env()
    memory_service.bind_model_runtime(model_client=model_client, model_router=model_router)
    persona_service.bind_model_runtime(model_client=model_client, model_router=model_router)
    restored_state = memory_service.restore_runtime_state()
    orchestrator = build_orchestrator(
        communication_hub=communication_hub,
        memory_service=memory_service,
        initial_state=restored_state,
        capability_registry=capability_registry,
        read_url_http_client=read_url_http_client,
        search_web_http_client=search_web_http_client,
        model_client=model_client,
        model_router=model_router,
    )
    existing_profile = persona_service.profile
    if existing_profile is not None:
        if not orchestrator.state.persona_id:
            orchestrator.state.persona_id = existing_profile.persona_id
        if not orchestrator.state.persona_summary:
            orchestrator.state.persona_summary = existing_profile.summary
        memory_service.update_persona_context(
            persona_summary=existing_profile.summary,
            relationship_state=existing_profile.relationship_context,
        )
    qq_adapter = QQAdapter(
        settings=qq_settings,
        orchestrator=orchestrator,
        communication_hub=communication_hub,
        http_client=qq_http_client,
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        await orchestrator.start_scheduler()
        await qq_adapter.start()
        try:
            yield
        finally:
            await qq_adapter.stop()
            await orchestrator.stop_scheduler()

    app = FastAPI(title="Amadeus", version="0.1.0", lifespan=lifespan)
    app.state.orchestrator = orchestrator
    app.state.model_routing = routing_settings
    app.state.model_router = model_router
    app.state.persona_service = persona_service
    app.state.qq_adapter = qq_adapter

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/api/persona/bootstrap", response_model=PersonaInspectionResponse)
    async def bootstrap_persona(request: PersonaBootstrapRequest) -> PersonaInspectionResponse:
        profile = await persona_service.bootstrap_from_text(
            request.seed_text,
            name=request.name,
        )
        orchestrator.state.persona_id = profile.persona_id
        orchestrator.state.persona_summary = profile.summary
        memory_service.update_persona_context(
            persona_summary=profile.summary,
            relationship_state=profile.relationship_context,
        )
        await memory_service.save_snapshot(orchestrator.state)
        return PersonaInspectionResponse(
            profile=profile,
            core_memory=memory_service.core_memory,
        )

    @app.get("/api/persona", response_model=PersonaInspectionResponse)
    async def get_persona() -> PersonaInspectionResponse:
        profile = persona_service.profile
        if profile is None:
            raise HTTPException(status_code=404, detail="Persona not initialized.")
        return PersonaInspectionResponse(
            profile=profile,
            core_memory=memory_service.core_memory,
        )

    @app.post("/api/messages", response_model=MessageLoopResponse)
    async def post_message(request: InboundMessageRequest) -> MessageLoopResponse:
        event = RuntimeEvent(
            event_type=EventType.MESSAGE_RECEIVED,
            source=EventSource.USER,
            payload={
                "user_id": request.user_id,
                "channel": request.channel,
                "text": request.text,
            },
        )
        await orchestrator.enqueue(event)
        outcome = await orchestrator.run_once()
        outbound_messages = [
            message.model_dump(mode="json") for message in communication_hub.drain_outbox()
        ]
        state = orchestrator.state
        next_step = orchestrator.next_pending_step()

        return MessageLoopResponse(
            event_id=event.event_id,
            outcome=outcome,
            outbound_messages=outbound_messages,
            state=RuntimeStateSummary(
                emotion_summary=state.emotion.summary,
                plan_summary=state.plan.day_summary,
                current_action_id=state.current_action_id,
                next_wake_at=orchestrator.next_wake_at().isoformat(),
                next_step_id=next_step.step_id if next_step else None,
                next_step_scheduled_for=next_step.scheduled_for if next_step else None,
                pending_event_ids=state.pending_event_ids,
            ),
        )

    @app.get("/api/runtime/state", response_model=RuntimeStateInspectionResponse)
    async def get_runtime_state() -> RuntimeStateInspectionResponse:
        latest_snapshot = memory_service.latest_snapshot()
        next_step = orchestrator.next_pending_step()
        return RuntimeStateInspectionResponse(
            state=orchestrator.state,
            latest_snapshot_id=latest_snapshot.snapshot_id if latest_snapshot else None,
            latest_snapshot_at=latest_snapshot.created_at if latest_snapshot else None,
            next_wake_at=orchestrator.next_wake_at().isoformat(),
            next_step_id=next_step.step_id if next_step else None,
            next_step_scheduled_for=next_step.scheduled_for if next_step else None,
        )

    @app.get("/api/memory", response_model=MemoryInspectionResponse)
    async def get_memory(limit: int = Query(default=10, ge=1, le=100)) -> MemoryInspectionResponse:
        latest_snapshot = memory_service.latest_snapshot()
        return MemoryInspectionResponse(
            core_memory=memory_service.core_memory,
            active_entries=memory_service.recent_active_entries(limit=limit),
            archive_entries=memory_service.recent_archive_entries(limit=limit),
            raw_entries=memory_service.recent_raw_entries(limit=limit),
            latest_snapshot_id=latest_snapshot.snapshot_id if latest_snapshot else None,
            latest_snapshot_at=latest_snapshot.created_at if latest_snapshot else None,
        )

    @app.get("/api/memory/search", response_model=MemorySearchResponse)
    async def search_memory(
        query: str = Query(min_length=1),
        top_k: int = Query(default=5, ge=1, le=20),
    ) -> MemorySearchResponse:
        active_hits, archive_hits = await memory_service.search_memory(
            query_text=query,
            top_k=top_k,
        )
        return MemorySearchResponse(
            query=query,
            active_hits=active_hits,
            archive_hits=archive_hits,
        )

    return app


app = create_app()
