from __future__ import annotations

import json
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, model_validator

from app.communication.hub import CommunicationHub
from app.core.events import EventSource, EventType, RuntimeEvent
from app.core.outcomes import (
    ActionOutcome,
    ExecutionTraceEntry,
    OutcomeStatus,
    ReplanDecision,
    ReplanKind,
    ToolInvocation,
)
from app.core.state import DayPlanBlock, PlanState, PlanStep, RuntimeState
from app.core.types import ExecutionGranularity, ExecutionMode, ExecutionZone
from app.front.executor_lab import (
    ExecutorLabDefaultsResponse,
    ExecutorLabRequest,
    ExecutorLabResponse,
    ExecutorLabRunner,
    empty_executor_lab_defaults,
)
from app.infra.embeddings import build_semantic_embedder
from app.infra.env import load_project_env, project_env_path, sync_process_env, update_project_env
from app.infra.model_client import (
    ModelClient,
    ModelRequest,
    ModelRouter,
    ModelTracePayload,
    PydanticAIModelClient,
)
from app.infra.settings import (
    ExecutionSettings,
    MCPSettings,
    MemoryEmbeddingSettings,
    MemoryRetrievalSettings,
    MemoryStorageSettings,
    ModelRole,
    ModelRoute,
    ModelRoutingSettings,
)
from app.mcp.registry import CapabilityRegistry
from app.memory.models import ActiveMemoryEntry, ArchiveMemoryEntry, CoreMemory, RawLogEntry
from app.memory.service import MemoryService
from app.persona.models import PersonaProfile
from app.persona.registry import PersonaCard, PersonaRegistry, PersonaWorkspace
from app.persona.service import PersonaService
from app.prompts.store import PromptStore
from app.runtime.clock import AdjustableClock, RuntimeClock
from app.runtime.contact_book import ContactBook
from app.runtime.execution import ExecutionService
from app.runtime.inspection import (
    MCPServerStatusSnapshot,
    RuntimeStateSnapshot,
    build_runtime_snapshot,
)
from app.runtime.interaction import InteractionService
from app.runtime.orchestrator import OrchestratorServices, RuntimeOrchestrator
from app.runtime.planning import PlanningService
from app.runtime.replan import ReplanService
from app.runtime.roleplay_agent import ModelRoleplayAgent
from app.runtime.scenario import (
    ScenarioReplayResult,
    ScenarioRunner,
    ScenarioStep,
    ScenarioTraceEntry,
)
from app.runtime.session import RuntimeSession
from app.tool.internal_provider import InternalProvider
from app.tool.mcp_provider import MCPProvider
from app.tool.models import ToolSpec


class InboundMessageRequest(BaseModel):
    user_id: str = "default-user"
    user_name: str | None = None
    channel: str = "api"
    text: str = Field(min_length=1)


class PersonaBootstrapRequest(BaseModel):
    name: str = "Amadeus"
    seed_text: str = Field(min_length=1)


class PersonaCardCreateRequest(BaseModel):
    name: str = Field(min_length=1)
    activate: bool = False


class PersonaCardUpdateRequest(BaseModel):
    name: str = Field(min_length=1)


class PersonaSoulUpdateRequest(BaseModel):
    soul_md: str = Field(min_length=1)


class MessageLoopResponse(BaseModel):
    event_id: str
    outcome: ActionOutcome | None = None
    outbound_messages: list[dict[str, str]] = Field(default_factory=list)
    state: RuntimeStateSnapshot


class RuntimeLoopResponse(BaseModel):
    outcome: ActionOutcome | None = None
    outbound_messages: list[dict[str, str]] = Field(default_factory=list)
    state: RuntimeStateSnapshot


class RuntimeClockSetRequest(BaseModel):
    at: datetime


class RuntimeClockAdvanceRequest(BaseModel):
    minutes: int = Field(default=0, ge=0, le=24 * 60)
    run_once: bool = False
    run_until_idle: bool = False
    max_iterations: int = Field(default=20, ge=1, le=200)

    @model_validator(mode="after")
    def validate_run_mode(self) -> RuntimeClockAdvanceRequest:
        if self.run_once and self.run_until_idle:
            raise ValueError("Choose either `run_once` or `run_until_idle`, not both.")
        return self


class RuntimeClockControlResponse(BaseModel):
    trace: list[ScenarioTraceEntry] = Field(default_factory=list)
    state: RuntimeStateSnapshot


class RuntimePlanRefreshResponse(BaseModel):
    event_id: str
    state: RuntimeStateSnapshot
    current_plan: PlanState


class ScenarioReplayRequest(BaseModel):
    steps: list[ScenarioStep] = Field(default_factory=list, min_length=1)


class RuntimeStateInspectionResponse(BaseModel):
    state: RuntimeState
    latest_snapshot_id: str | None = None
    latest_snapshot_at: str | None = None
    next_wake_at: str | None = None
    next_step_id: str | None = None
    next_step_scheduled_for: str | None = None
    summary: RuntimeStateSnapshot


class RuntimeDebugControlsResponse(BaseModel):
    supports_run_once: bool = True
    supports_pause_resume: bool = False
    supports_clock_control: bool = False


class RuntimeDebugExecutionResponse(BaseModel):
    raw_entry_id: str
    recorded_at: str
    step: PlanStep
    outcome: ActionOutcome
    loop_stop_reason: str | None = None


class RuntimeDebugReplanResponse(BaseModel):
    raw_entry_id: str
    recorded_at: str
    decision: ReplanDecision
    event_type: str = "none"
    outcome_summary: str = ""


class RuntimeDebugErrorResponse(BaseModel):
    message: str | None = None
    latest_failed_execution: RuntimeDebugExecutionResponse | None = None


class RuntimeDebugResponse(BaseModel):
    summary: RuntimeStateSnapshot
    current_plan: PlanState
    controls: RuntimeDebugControlsResponse
    latest_execution: RuntimeDebugExecutionResponse | None = None
    latest_replan: RuntimeDebugReplanResponse | None = None
    latest_error: RuntimeDebugErrorResponse = Field(default_factory=RuntimeDebugErrorResponse)
    tools: list[ToolSpec] = Field(default_factory=list)
    mcp_servers: list[MCPServerStatusSnapshot] = Field(default_factory=list)


class PlanLabDebugResponse(BaseModel):
    summary: RuntimeStateSnapshot
    current_plan: PlanState
    core_memory: CoreMemory
    latest_execution: RuntimeDebugExecutionResponse | None = None
    latest_replan: RuntimeDebugReplanResponse | None = None
    planning_entries: list[RawLogEntry] = Field(default_factory=list)
    replan_entries: list[RawLogEntry] = Field(default_factory=list)
    model_entries: list[RawLogEntry] = Field(default_factory=list)


class PlanLabDayStartRequest(BaseModel):
    persona_name: str = "Amadeus"
    soul_md: str = ""
    memories: list[str] = Field(default_factory=list)
    note: str = ""


class PlanLabReplanDecisionRequest(BaseModel):
    persona_name: str = "Amadeus"
    soul_md: str = ""
    memories: list[str] = Field(default_factory=list)
    outcome_content: str = Field(min_length=1)
    event_type: EventType = EventType.ACTION_COMPLETED
    event_text: str = ""
    execution_mode: ExecutionMode = ExecutionMode.NARRATIVE
    execution_zone: ExecutionZone = ExecutionZone.NON_REAL


class PlanLabReplanDecisionResponse(BaseModel):
    decision: ReplanDecision
    latest_replan: RuntimeDebugReplanResponse | None = None


class PlanLabApplyReplanRequest(BaseModel):
    persona_name: str = "Amadeus"
    soul_md: str = ""
    memories: list[str] = Field(default_factory=list)
    kind: ReplanKind
    reason: str = ""
    event_text: str = ""
    outcome_content: str = "Manual plan-lab replan."
    execution_mode: ExecutionMode = ExecutionMode.NARRATIVE
    execution_zone: ExecutionZone = ExecutionZone.NON_REAL


class ModelTraceDebugEntryResponse(BaseModel):
    raw_entry_id: str
    recorded_at: str
    trace: ModelTracePayload


class ModelTraceDebugResponse(BaseModel):
    recent_traces: list[ModelTraceDebugEntryResponse] = Field(default_factory=list)
    latest_trace: ModelTraceDebugEntryResponse | None = None


class ModelRouteConfigResponse(BaseModel):
    provider: str = ""
    model: str = ""
    api_key: str = ""
    base_url: str = ""
    configured: bool = False


class ModelConfigResponse(BaseModel):
    dialogue: ModelRouteConfigResponse = Field(default_factory=ModelRouteConfigResponse)
    executor: ModelRouteConfigResponse = Field(default_factory=ModelRouteConfigResponse)
    decision: ModelRouteConfigResponse = Field(default_factory=ModelRouteConfigResponse)
    memory: ModelRouteConfigResponse = Field(default_factory=ModelRouteConfigResponse)
    env_path: str = ""


class ModelConfigUpdateRequest(BaseModel):
    dialogue: ModelRouteConfigResponse
    executor: ModelRouteConfigResponse
    decision: ModelRouteConfigResponse
    memory: ModelRouteConfigResponse


class ModelConnectionTestRequest(BaseModel):
    role: ModelRole
    route: ModelRouteConfigResponse


class ModelConnectionTestResponse(BaseModel):
    ok: bool
    role: ModelRole
    route: ModelRouteConfigResponse
    provider_name: str | None = None
    response_text: str | None = None
    error: str | None = None


class ToolDebugInvocationResponse(BaseModel):
    raw_entry_id: str
    recorded_at: str
    step_id: str
    step_title: str
    execution_status: OutcomeStatus
    execution_source: str
    invocation_index: int
    tool_name: str
    arguments: dict[str, object] = Field(default_factory=dict)
    status: OutcomeStatus
    detail: str = ""
    source_type: str = ""
    source_id: str = ""
    metadata: dict[str, object] = Field(default_factory=dict)
    result: dict[str, object] = Field(default_factory=dict)


class ToolDebugResponse(BaseModel):
    tools: list[ToolSpec] = Field(default_factory=list)
    recent_invocations: list[ToolDebugInvocationResponse] = Field(default_factory=list)
    mcp_servers: list[MCPServerStatusSnapshot] = Field(default_factory=list)


class HealthResponse(BaseModel):
    status: str
    scheduler_running: bool
    scheduler_paused: bool
    started_at: str
    mcp_configured_server_count: int = 0
    mcp_connected_server_count: int = 0
    mcp_registered_tool_count: int = 0
    mcp_servers: list[MCPServerStatusSnapshot] = Field(default_factory=list)


class RuntimeLifecycleControlResponse(BaseModel):
    status: str
    scheduler_running: bool
    scheduler_paused: bool
    state: RuntimeStateSnapshot


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


class MemoryDebugEntryResponse(BaseModel):
    entry_id: str
    created_at: str
    source: str
    interaction_partner: str | None = None
    content: str


class MemoryDebugStageHitResponse(MemoryDebugEntryResponse):
    stage: str
    score: float


class MemoryDebugCandidateResponse(MemoryDebugEntryResponse):
    score: float
    hit_stages: list[str] = Field(default_factory=list)


class MemorySearchDebugBucketResponse(BaseModel):
    settings: dict[str, object] = Field(default_factory=dict)
    stage_hits: dict[str, list[MemoryDebugStageHitResponse]] = Field(default_factory=dict)
    combined_candidates: list[MemoryDebugCandidateResponse] = Field(default_factory=list)
    reranked_entry_ids: list[str] = Field(default_factory=list)
    final_entries: list[MemoryDebugEntryResponse] = Field(default_factory=list)


class MemorySearchDebugResponse(BaseModel):
    query: str
    active: MemorySearchDebugBucketResponse
    archive: MemorySearchDebugBucketResponse


class PersonaInspectionResponse(BaseModel):
    profile: PersonaProfile
    core_memory: CoreMemory


class PersonaCardListResponse(BaseModel):
    active_persona_key: str | None = None
    cards: list[PersonaCard] = Field(default_factory=list)


class PersonaCardDetailResponse(BaseModel):
    card: PersonaCard
    profile: PersonaProfile | None = None
    soul_md: str = ""
    core_memory: CoreMemory | None = None


class PersonaActivationResponse(BaseModel):
    card: PersonaCard
    state: RuntimeStateSnapshot
    core_memory: CoreMemory


class WorkspaceExecutionRecordResponse(BaseModel):
    raw_entry_id: str
    recorded_at: str
    step_id: str
    title: str
    detail: str = ""
    status: OutcomeStatus
    summary: str
    stop_reason: str | None = None
    trace: list[ExecutionTraceEntry] = Field(default_factory=list)
    tool_invocations: list[ToolInvocation] = Field(default_factory=list)
    raw_data: dict[str, object] = Field(default_factory=dict)


class WorkspacePlanItemResponse(BaseModel):
    item_id: str
    kind: str
    label: str
    time_label: str = ""
    status: str
    step_id: str | None = None
    active: bool = False
    current: bool = False
    execution_records: list[WorkspaceExecutionRecordResponse] = Field(default_factory=list)


class WorkspaceWorkbenchResponse(BaseModel):
    summary: RuntimeStateSnapshot
    state: RuntimeState
    current_plan: PlanState
    persona_name: str = ""
    latest_execution: RuntimeDebugExecutionResponse | None = None
    latest_replan: RuntimeDebugReplanResponse | None = None
    plan_items: list[WorkspacePlanItemResponse] = Field(default_factory=list)
    roleplay_context_preview: str = ""


class WorkspaceChatEntryResponse(BaseModel):
    entry_id: str
    created_at: str
    direction: str = ""
    channel: str = ""
    partner_name: str = ""
    speaker: str = ""
    content: str = ""
    raw_content: str = ""
    metadata: dict[str, object] = Field(default_factory=dict)


class WorkspaceChatResponse(BaseModel):
    persona_name: str = ""
    entries: list[WorkspaceChatEntryResponse] = Field(default_factory=list)
    roleplay_context_preview: str = ""


class PromptFileSummaryResponse(BaseModel):
    path: str
    title: str
    updated_at: str


class PromptFileContentResponse(BaseModel):
    path: str
    content: str


class PromptFileUpdateRequest(BaseModel):
    content: str


def _mcp_server_snapshots(mcp_provider: object | None) -> list[MCPServerStatusSnapshot]:
    if mcp_provider is None:
        return []
    inspector = getattr(mcp_provider, "server_status", None)
    if inspector is None:
        return []
    try:
        statuses = inspector()
    except Exception:
        return []
    if not isinstance(statuses, list):
        return []

    snapshots: list[MCPServerStatusSnapshot] = []
    for item in statuses:
        if not isinstance(item, dict):
            continue
        registered_tools = item.get("registered_tools", [])
        if not isinstance(registered_tools, list):
            registered_tools = []
        snapshots.append(
            MCPServerStatusSnapshot(
                server_id=str(item.get("server_id", "")),
                transport=str(item.get("transport", "")),
                connected=bool(item.get("connected", False)),
                registered_tools=[str(name) for name in registered_tools],
                tool_count=int(item.get("tool_count", 0)),
            )
        )
    return snapshots


def _latest_raw_entry(memory_service: object, kind: str) -> RawLogEntry | None:
    entries = getattr(memory_service, "raw_entries", None)
    if not isinstance(entries, list):
        return None
    for entry in reversed(entries):
        if isinstance(entry, RawLogEntry) and entry.kind == kind:
            return entry
    return None


def _recent_raw_entries_by_kind(
    memory_service: object,
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


def _model_trace_debug_entry(entry: RawLogEntry) -> ModelTraceDebugEntryResponse | None:
    try:
        trace = ModelTracePayload.model_validate(entry.payload)
    except Exception:
        return None
    return ModelTraceDebugEntryResponse(
        raw_entry_id=entry.entry_id,
        recorded_at=entry.created_at,
        trace=trace,
    )


def _recent_model_traces(
    memory_service: object,
    *,
    limit: int,
) -> list[ModelTraceDebugEntryResponse]:
    entries = getattr(memory_service, "raw_entries", None)
    if not isinstance(entries, list):
        return []
    traces: list[ModelTraceDebugEntryResponse] = []
    for entry in reversed(entries):
        if not isinstance(entry, RawLogEntry) or entry.kind != "model_io":
            continue
        normalized = _model_trace_debug_entry(entry)
        if normalized is None:
            continue
        traces.append(normalized)
        if len(traces) >= limit:
            break
    return traces


def _model_route_config(route: ModelRoute) -> ModelRouteConfigResponse:
    return ModelRouteConfigResponse(
        provider=route.provider,
        model=route.model,
        api_key=route.api_key,
        base_url=route.base_url,
        configured=route.is_configured(),
    )


def _apply_model_route_config(route: ModelRoute, config: ModelRouteConfigResponse) -> None:
    route.provider = config.provider.strip()
    route.model = config.model.strip()
    route.api_key = config.api_key.strip()
    route.base_url = config.base_url.strip()


def _model_route_env_values(config: ModelConfigUpdateRequest) -> dict[str, str]:
    return {
        "AMADEUS_DIALOGUE_PROVIDER": config.dialogue.provider.strip(),
        "AMADEUS_DIALOGUE_MODEL": config.dialogue.model.strip(),
        "AMADEUS_DIALOGUE_API_KEY": config.dialogue.api_key.strip(),
        "AMADEUS_DIALOGUE_BASE_URL": config.dialogue.base_url.strip(),
        "AMADEUS_EXECUTOR_PROVIDER": config.executor.provider.strip(),
        "AMADEUS_EXECUTOR_MODEL": config.executor.model.strip(),
        "AMADEUS_EXECUTOR_API_KEY": config.executor.api_key.strip(),
        "AMADEUS_EXECUTOR_BASE_URL": config.executor.base_url.strip(),
        "AMADEUS_DECISION_PROVIDER": config.decision.provider.strip(),
        "AMADEUS_DECISION_MODEL": config.decision.model.strip(),
        "AMADEUS_DECISION_API_KEY": config.decision.api_key.strip(),
        "AMADEUS_DECISION_BASE_URL": config.decision.base_url.strip(),
        "AMADEUS_MEMORY_PROVIDER": config.memory.provider.strip(),
        "AMADEUS_MEMORY_MODEL": config.memory.model.strip(),
        "AMADEUS_MEMORY_API_KEY": config.memory.api_key.strip(),
        "AMADEUS_MEMORY_BASE_URL": config.memory.base_url.strip(),
    }


def _latest_execution_debug(memory_service: object) -> RuntimeDebugExecutionResponse | None:
    entry = _latest_raw_entry(memory_service, "outcome")
    if entry is None:
        return None
    step_payload = entry.payload.get("step")
    outcome_payload = entry.payload.get("outcome")
    if not isinstance(step_payload, dict) or not isinstance(outcome_payload, dict):
        return None
    step = PlanStep.model_validate(step_payload)
    outcome = ActionOutcome.model_validate(outcome_payload)
    loop_stop_reason = outcome.raw_data.get("loop_stop_reason")
    return RuntimeDebugExecutionResponse(
        raw_entry_id=entry.entry_id,
        recorded_at=entry.created_at,
        step=step,
        outcome=outcome,
        loop_stop_reason=str(loop_stop_reason) if loop_stop_reason is not None else None,
    )


def _latest_replan_debug(memory_service: object) -> RuntimeDebugReplanResponse | None:
    entry = _latest_raw_entry(memory_service, "replan")
    if entry is None:
        return None
    decision_payload = entry.payload.get("decision")
    if not isinstance(decision_payload, dict):
        return None
    decision = ReplanDecision.model_validate(decision_payload)
    return RuntimeDebugReplanResponse(
        raw_entry_id=entry.entry_id,
        recorded_at=entry.created_at,
        decision=decision,
        event_type=str(entry.payload.get("event_type", "none")),
        outcome_summary=str(entry.payload.get("outcome_summary", "")),
    )


def _workspace_execution_records(
    memory_service: object,
    *,
    limit: int = 80,
) -> list[WorkspaceExecutionRecordResponse]:
    entries = getattr(memory_service, "raw_entries", None)
    if not isinstance(entries, list):
        return []
    records: list[WorkspaceExecutionRecordResponse] = []
    for entry in reversed(entries):
        if not isinstance(entry, RawLogEntry) or entry.kind != "outcome":
            continue
        step_payload = entry.payload.get("step")
        outcome_payload = entry.payload.get("outcome")
        if not isinstance(step_payload, dict) or not isinstance(outcome_payload, dict):
            continue
        try:
            step = PlanStep.model_validate(step_payload)
            outcome = ActionOutcome.model_validate(outcome_payload)
        except Exception:
            continue
        stop_reason = outcome.raw_data.get("loop_stop_reason")
        records.append(
            WorkspaceExecutionRecordResponse(
                raw_entry_id=entry.entry_id,
                recorded_at=entry.created_at,
                step_id=step.step_id,
                title=step.title,
                detail=step.detail,
                status=outcome.status,
                summary=outcome.content,
                stop_reason=str(stop_reason) if stop_reason is not None else None,
                trace=outcome.execution_trace,
                tool_invocations=outcome.tool_invocations,
                raw_data={
                    str(key): value
                    for key, value in outcome.raw_data.items()
                },
            )
        )
        if len(records) >= limit:
            break
    records.reverse()
    return records


def _workspace_plan_items(
    *,
    state: RuntimeState,
    execution_records: list[WorkspaceExecutionRecordResponse],
) -> list[WorkspacePlanItemResponse]:
    records_by_step_id: dict[str, list[WorkspaceExecutionRecordResponse]] = {}
    for record in execution_records:
        records_by_step_id.setdefault(record.step_id, []).append(record)

    items: list[WorkspacePlanItemResponse] = []
    if state.plan.day_blocks:
        for block in state.plan.day_blocks:
            step_id = f"block_{block.block_id}"
            items.append(
                WorkspacePlanItemResponse(
                    item_id=block.block_id,
                    kind="day_block",
                    label=block.label,
                    time_label=block.time,
                    status=block.status.value,
                    step_id=step_id,
                    active=state.plan.active_block_id == block.block_id,
                    current=state.current_action_id == step_id,
                    execution_records=records_by_step_id.get(step_id, []),
                )
            )
        return items

    for step in state.plan.minute_steps:
        items.append(
            WorkspacePlanItemResponse(
                item_id=step.step_id,
                kind="minute_step",
                label=step.title,
                time_label=step.scheduled_for or "",
                status=step.status.value,
                step_id=step.step_id,
                active=state.current_action_id == step.step_id,
                current=state.current_action_id == step.step_id,
                execution_records=records_by_step_id.get(step.step_id, []),
            )
        )
    return items


def _workspace_chat_entries(
    memory_service: object,
    *,
    persona_name: str,
    limit: int = 80,
) -> list[WorkspaceChatEntryResponse]:
    getter = getattr(memory_service, "get_persisted_roleplay_agent_context", None)
    if not callable(getter):
        return []
    context = getter()
    raw_entries = getattr(context, "entries", [])
    if not isinstance(raw_entries, list):
        return []

    messages: list[WorkspaceChatEntryResponse] = []
    for entry in raw_entries:
        kind = str(getattr(entry, "kind", "")).strip()
        raw_content = str(getattr(entry, "content", "")).strip()
        metadata = getattr(entry, "metadata", {})
        if kind != "interaction_record" or not raw_content:
            continue
        if not isinstance(metadata, dict):
            metadata = {}
        direction = str(metadata.get("direction", "")).strip()
        channel = str(metadata.get("channel", "")).strip()
        partner_name = str(metadata.get("interaction_partner", "")).strip()
        lines = [line.strip() for line in raw_content.splitlines() if line.strip()]
        speaker = partner_name if direction == "incoming" else (persona_name.strip() or "角色")
        content = raw_content
        for line in reversed(lines):
            if line.startswith("【") and line.endswith("】"):
                continue
            if ":" in line:
                maybe_speaker, maybe_content = line.split(":", 1)
                if maybe_content.strip():
                    speaker = maybe_speaker.strip() or speaker
                    content = maybe_content.strip()
                    break
                continue
            content = line
            break
        messages.append(
            WorkspaceChatEntryResponse(
                entry_id=str(getattr(entry, "created_at", "")) + ":" + direction,
                created_at=str(getattr(entry, "created_at", "")),
                direction=direction,
                channel=channel,
                partner_name=partner_name,
                speaker=speaker,
                content=content,
                raw_content=raw_content,
                metadata={
                    str(key): value
                    for key, value in metadata.items()
                },
            )
        )
    return messages[-limit:]


def _tool_specs(orchestrator: RuntimeOrchestrator) -> list[ToolSpec]:
    registry = getattr(orchestrator.services.execution, "tool_registry", None)
    if registry is None:
        return []
    list_tools = getattr(registry, "list_tools", None)
    if not callable(list_tools):
        return []
    tools = list_tools()
    if not isinstance(tools, list):
        return []
    return [tool for tool in tools if isinstance(tool, ToolSpec)]


def _tool_specs_by_name(orchestrator: RuntimeOrchestrator) -> dict[str, ToolSpec]:
    return {tool.name: tool for tool in _tool_specs(orchestrator)}


def _recent_tool_invocations(
    memory_service: object,
    *,
    orchestrator: RuntimeOrchestrator,
    limit: int,
) -> list[ToolDebugInvocationResponse]:
    entries = getattr(memory_service, "raw_entries", None)
    if not isinstance(entries, list):
        return []
    tools_by_name = _tool_specs_by_name(orchestrator)
    invocations: list[ToolDebugInvocationResponse] = []
    for entry in reversed(entries):
        if not isinstance(entry, RawLogEntry) or entry.kind != "outcome":
            continue
        step_payload = entry.payload.get("step")
        outcome_payload = entry.payload.get("outcome")
        if not isinstance(step_payload, dict) or not isinstance(outcome_payload, dict):
            continue
        try:
            step = PlanStep.model_validate(step_payload)
            outcome = ActionOutcome.model_validate(outcome_payload)
        except Exception:
            continue
        raw_results: list[dict[str, object]] = []
        initial_result = outcome.raw_data.get("result")
        if isinstance(initial_result, dict):
            raw_results.append(initial_result)
        loop_results = outcome.raw_data.get("loop_tool_results")
        if isinstance(loop_results, list):
            raw_results.extend(
                result
                for result in loop_results
                if isinstance(result, dict)
            )
        for invocation_index, invocation in enumerate(outcome.tool_invocations, start=1):
            tool_spec = tools_by_name.get(invocation.capability)
            result_payload = (
                raw_results[invocation_index - 1]
                if invocation_index - 1 < len(raw_results)
                else {}
            )
            invocations.append(
                ToolDebugInvocationResponse(
                    raw_entry_id=entry.entry_id,
                    recorded_at=entry.created_at,
                    step_id=step.step_id,
                    step_title=step.title,
                    execution_status=outcome.status,
                    execution_source=outcome.source.value,
                    invocation_index=invocation_index,
                    tool_name=invocation.capability,
                    arguments={
                        str(key): value for key, value in invocation.arguments.items()
                    },
                    status=invocation.status,
                    detail=invocation.detail,
                    source_type=(
                        tool_spec.source_type.value if tool_spec is not None else ""
                    ),
                    source_id=tool_spec.source_id if tool_spec is not None else "",
                    metadata=tool_spec.metadata if tool_spec is not None else {},
                    result=result_payload,
                )
            )
            if len(invocations) >= limit:
                return invocations
    return invocations


def _memory_debug_bucket(payload: object) -> MemorySearchDebugBucketResponse:
    if not isinstance(payload, dict):
        return MemorySearchDebugBucketResponse()
    stage_hits_payload = payload.get("stage_hits", {})
    stage_hits: dict[str, list[MemoryDebugStageHitResponse]] = {}
    if isinstance(stage_hits_payload, dict):
        for stage_name, hits in stage_hits_payload.items():
            if not isinstance(hits, list):
                continue
            stage_hits[str(stage_name)] = [
                MemoryDebugStageHitResponse.model_validate(hit)
                for hit in hits
                if isinstance(hit, dict)
            ]
    combined_candidates_payload = payload.get("combined_candidates", [])
    final_entries_payload = payload.get("final_entries", [])
    reranked_entry_ids = payload.get("reranked_entry_ids", [])
    settings = payload.get("settings", {})
    return MemorySearchDebugBucketResponse(
        settings=settings if isinstance(settings, dict) else {},
        stage_hits=stage_hits,
        combined_candidates=[
            MemoryDebugCandidateResponse.model_validate(candidate)
            for candidate in combined_candidates_payload
            if isinstance(candidate, dict)
        ],
        reranked_entry_ids=[
            str(entry_id)
            for entry_id in reranked_entry_ids
            if isinstance(entry_id, (str, int, float))
        ],
        final_entries=[
            MemoryDebugEntryResponse.model_validate(entry)
            for entry in final_entries_payload
            if isinstance(entry, dict)
        ],
    )


def build_orchestrator(
    communication_hub: CommunicationHub,
    memory_service: MemoryService,
    *,
    initial_state: RuntimeState | None = None,
    clock: RuntimeClock | None = None,
    capability_registry: CapabilityRegistry | None = None,
    read_url_http_client: httpx.AsyncClient | None = None,
    search_web_http_client: httpx.AsyncClient | None = None,
    model_client: ModelClient | None = None,
    model_router: ModelRouter | None = None,
    execution_settings: ExecutionSettings | None = None,
    prompt_store: PromptStore | None = None,
) -> RuntimeOrchestrator:
    registry = capability_registry or CapabilityRegistry()
    contact_book = ContactBook()
    InternalProvider(
        read_url_http_client=read_url_http_client,
        search_web_http_client=search_web_http_client,
        contact_book=contact_book,
    ).register_tools(
        registry,
    )
    services_roleplay_agent = ModelRoleplayAgent(
        model_client=model_client,
        model_router=model_router,
    )
    return RuntimeOrchestrator(
        services=OrchestratorServices(
            planning=PlanningService(
                model_client=model_client,
                model_router=model_router,
                memory_service=memory_service,
                prompt_store=prompt_store,
                execution_granularity=(
                    execution_settings.execution_granularity
                    if execution_settings is not None
                    else ExecutionGranularity.MINUTE
                ),
            ),
            execution=ExecutionService(
                registry,
                model_client=model_client,
                model_router=model_router,
                memory_service=memory_service,
                roleplay_agent=services_roleplay_agent,
                max_inner_loop_turns=(
                    execution_settings.max_inner_loop_turns if execution_settings is not None else 7
                ),
                loop_pre_replan_buffer_seconds=(
                    execution_settings.loop_pre_replan_buffer_seconds
                    if execution_settings is not None
                    else 30
                ),
            ),
            replan=ReplanService(
                model_client=model_client,
                model_router=model_router,
                memory_service=memory_service,
                prompt_store=prompt_store,
            ),
            interaction=InteractionService(
                memory_service=memory_service,
                roleplay_agent=services_roleplay_agent,
                contact_book=contact_book,
            ),
            memory=memory_service,
            communication=communication_hub,
        ),
        initial_state=initial_state,
        clock=clock,
        interaction_cooldown_seconds=(
            execution_settings.interaction_cooldown_seconds
            if execution_settings is not None
            else 180
        ),
    )


def create_app(
    *,
    communication_hub: CommunicationHub | None = None,
    memory_service: MemoryService | None = None,
    persona_service: PersonaService | None = None,
    persona_registry: PersonaRegistry | None = None,
    persona_registry_path: Path | None = None,
    persona_workspace_root: Path | None = None,
    runtime_clock: RuntimeClock | None = None,
    routing_settings: ModelRoutingSettings | None = None,
    execution_settings: ExecutionSettings | None = None,
    retrieval_settings: MemoryRetrievalSettings | None = None,
    storage_settings: MemoryStorageSettings | None = None,
    embedding_settings: MemoryEmbeddingSettings | None = None,
    mcp_settings: MCPSettings | None = None,
    capability_registry: CapabilityRegistry | None = None,
    mcp_provider: MCPProvider | None = None,
    read_url_http_client: httpx.AsyncClient | None = None,
    search_web_http_client: httpx.AsyncClient | None = None,
    model_client: ModelClient | None = None,
    prompt_root: Path | None = None,
    app_title: str = "Amadeus",
    default_front_page: str = "workspace.html",
    auto_start_scheduler: bool = True,
    restore_runtime_state: bool = True,
) -> FastAPI:
    load_project_env()
    routing_settings = routing_settings or ModelRoutingSettings.from_env()
    execution_settings = execution_settings or ExecutionSettings.from_env()
    retrieval_settings = retrieval_settings or MemoryRetrievalSettings.from_env()
    storage_settings = storage_settings or MemoryStorageSettings.from_env()
    embedding_settings = embedding_settings or MemoryEmbeddingSettings.from_env()
    mcp_settings = mcp_settings or MCPSettings.from_env()
    model_router = ModelRouter(settings=routing_settings)
    model_client = model_client or PydanticAIModelClient()
    communication_hub = communication_hub or CommunicationHub()
    runtime_clock = runtime_clock or AdjustableClock(tick_real_time=False)
    mcp_provider = mcp_provider or MCPProvider(servers=mcp_settings.servers)
    capability_registry = capability_registry or CapabilityRegistry()
    semantic_embedder = build_semantic_embedder(embedding_settings)
    prompt_store = PromptStore(prompt_root)
    if persona_registry is None and (
        persona_registry_path is not None
        or persona_workspace_root is not None
        or (memory_service is None and persona_service is None)
    ):
        persona_registry = PersonaRegistry(
            index_path=persona_registry_path,
            workspace_root=persona_workspace_root,
        )

    def _build_memory_service_for_workspace(workspace: PersonaWorkspace) -> MemoryService:
        return MemoryService(
            raw_log_path=workspace.raw_log_path,
            snapshot_path=workspace.snapshot_path,
            active_memory_path=workspace.active_memory_path,
            core_memory_path=workspace.core_memory_path,
            roleplay_context_path=workspace.roleplay_context_path,
            archive_memory_path=workspace.archive_memory_path,
            storage_settings=storage_settings,
            retrieval_settings=retrieval_settings,
            model_client=model_client,
            model_router=model_router,
            prompt_store=prompt_store,
            semantic_entry_embedder=semantic_embedder,
            semantic_query_embedder=semantic_embedder,
        )

    def _build_persona_service_for_workspace(workspace: PersonaWorkspace) -> PersonaService:
        return PersonaService(
            soul_path=workspace.soul_path,
            model_client=model_client,
            model_router=model_router,
            prompt_store=prompt_store,
        )

    def _sync_session_persona_context(session: RuntimeSession, *, overwrite: bool) -> None:
        profile = session.persona_service.profile
        if profile is None:
            return
        if overwrite or not session.orchestrator.state.persona_name:
            session.orchestrator.state.persona_name = profile.name
        if overwrite or not session.orchestrator.state.persona_summary:
            session.orchestrator.state.persona_summary = session.persona_service.summary
        _sync_persona_memory_context(
            session.memory_service,
            session.persona_service,
        )

    def _sync_persona_memory_context(
        memory_service: MemoryService,
        persona_service: PersonaService,
    ) -> None:
        memory_service.update_persona_context(
            soul_md=persona_service.soul_markdown,
        )

    def _reset_core_memory_to_soul(
        memory_service: MemoryService,
        persona_service: PersonaService,
    ) -> CoreMemory:
        return memory_service.reset_core_memory(
            soul_md=persona_service.soul_markdown,
        )

    def _memory_inspection_response(
        memory_service: MemoryService,
        *,
        limit: int,
    ) -> MemoryInspectionResponse:
        latest_snapshot = memory_service.latest_snapshot()
        return MemoryInspectionResponse(
            core_memory=memory_service.core_memory,
            active_entries=memory_service.recent_active_entries(limit=limit),
            archive_entries=memory_service.recent_archive_entries(limit=limit),
            raw_entries=memory_service.recent_raw_entries(limit=limit),
            latest_snapshot_id=latest_snapshot.snapshot_id if latest_snapshot else None,
            latest_snapshot_at=latest_snapshot.created_at if latest_snapshot else None,
        )

    def _apply_plan_lab_manual_context(
        *,
        orchestrator: RuntimeOrchestrator,
        memory_service: MemoryService,
        persona_name: str,
        soul_md: str,
        memories: list[str],
    ) -> None:
        cleaned_persona_name = " ".join(persona_name.split()).strip()
        if cleaned_persona_name:
            orchestrator.state.persona_name = cleaned_persona_name

        cleaned_memories = [
            str(item).strip()
            for item in memories
            if str(item).strip()
        ]
        if soul_md.strip():
            memory_service.update_persona_context(
                soul_md=soul_md.strip(),
            )
        if cleaned_memories:
            setter = getattr(memory_service, "set_manual_context_memories", None)
            if callable(setter):
                setter(cleaned_memories)

    def _build_runtime_session(
        *,
        workspace: PersonaWorkspace | None,
        initial_memory_service: MemoryService | None = None,
        initial_persona_service: PersonaService | None = None,
        persona_key: str | None = None,
    ) -> RuntimeSession:
        current_memory_service = initial_memory_service
        if current_memory_service is None:
            current_memory_service = (
                _build_memory_service_for_workspace(workspace)
                if workspace is not None
                else MemoryService(
                    model_client=model_client,
                    model_router=model_router,
                    storage_settings=storage_settings,
                    retrieval_settings=retrieval_settings,
                    prompt_store=prompt_store,
                    semantic_entry_embedder=semantic_embedder,
                    semantic_query_embedder=semantic_embedder,
                )
            )
        current_persona_service = initial_persona_service
        if current_persona_service is None:
            current_persona_service = (
                _build_persona_service_for_workspace(workspace)
                if workspace is not None
                else PersonaService(
                    model_client=model_client,
                    model_router=model_router,
                    prompt_store=prompt_store,
                )
            )
        current_memory_service.bind_model_runtime(
            model_client=model_client,
            model_router=model_router,
        )
        current_persona_service.bind_model_runtime(
            model_client=model_client,
            model_router=model_router,
        )
        restored_state = (
            current_memory_service.restore_runtime_state()
            if restore_runtime_state
            else None
        )
        current_orchestrator = build_orchestrator(
            communication_hub=communication_hub,
            memory_service=current_memory_service,
            initial_state=restored_state,
            clock=runtime_clock,
            capability_registry=capability_registry,
            read_url_http_client=read_url_http_client,
            search_web_http_client=search_web_http_client,
            model_client=model_client,
            model_router=model_router,
            execution_settings=execution_settings,
            prompt_store=prompt_store,
        )
        current_scenario_runner = ScenarioRunner(
            orchestrator=current_orchestrator,
            communication_hub=communication_hub,
        )
        session = RuntimeSession(
            persona_key=persona_key,
            persona_service=current_persona_service,
            memory_service=current_memory_service,
            orchestrator=current_orchestrator,
            scenario_runner=current_scenario_runner,
        )
        _sync_session_persona_context(session, overwrite=False)
        return session

    active_workspace = persona_registry.active_workspace() if persona_registry is not None else None
    runtime_session = (
        _build_runtime_session(
            workspace=active_workspace,
            persona_key=active_workspace.persona_key,
        )
        if active_workspace is not None
        else _build_runtime_session(
            workspace=None,
            initial_memory_service=memory_service,
            initial_persona_service=persona_service,
        )
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        current_session: RuntimeSession = app.state.runtime_session
        await mcp_provider.register_tools(
            current_session.orchestrator.services.execution.tool_registry
        )
        if auto_start_scheduler:
            await current_session.orchestrator.start_scheduler()
        else:
            await current_session.orchestrator.pause_scheduler()
        app.state.runtime_started = True
        try:
            yield
        finally:
            app.state.runtime_started = False
            latest_session: RuntimeSession = app.state.runtime_session
            await latest_session.orchestrator.stop_scheduler()
            await mcp_provider.close()

    app = FastAPI(title=app_title, version="0.1.0", lifespan=lifespan)
    front_root = Path(__file__).resolve().parent / "front"
    app.mount(
        "/front/assets",
        StaticFiles(directory=front_root / "assets"),
        name="front-assets",
    )
    app.mount(
        "/assets",
        StaticFiles(directory=front_root / "assets"),
        name="assets",
    )
    app.state.runtime_session = runtime_session
    app.state.orchestrator = runtime_session.orchestrator
    app.state.persona_service = runtime_session.persona_service
    app.state.memory_service = runtime_session.memory_service
    app.state.persona_registry = persona_registry
    app.state.mcp_provider = mcp_provider
    app.state.scenario_runner = runtime_session.scenario_runner
    app.state.runtime_started = False
    app.state.prompt_store = prompt_store

    def _bind_model_trace_sink(memory_service: MemoryService) -> None:
        binder = getattr(model_client, "bind_trace_sink", None)
        if callable(binder):
            binder(memory_service.record_model_trace)

    _bind_model_trace_sink(runtime_session.memory_service)

    def _current_session() -> RuntimeSession:
        return app.state.runtime_session

    def _current_orchestrator() -> RuntimeOrchestrator:
        return _current_session().orchestrator

    def _current_memory_service() -> MemoryService:
        return _current_session().memory_service

    def _current_persona_service() -> PersonaService:
        return _current_session().persona_service

    def _current_scenario_runner() -> ScenarioRunner:
        return _current_session().scenario_runner

    def _require_persona_registry() -> PersonaRegistry:
        registry = app.state.persona_registry
        if registry is None:
            raise HTTPException(status_code=409, detail="Persona registry is not configured.")
        return registry

    def _sync_registry_card(persona_key: str | None, profile: PersonaProfile | None) -> None:
        if persona_key is None or persona_registry is None:
            return
        persona_registry.update_card_from_profile(persona_key, profile)

    def _build_card_detail(persona_key: str) -> PersonaCardDetailResponse:
        registry = _require_persona_registry()
        card = registry.get_card(persona_key)
        if card is None:
            raise HTTPException(status_code=404, detail="Persona card not found.")
        session = _current_session()
        if session.persona_key == persona_key:
            return PersonaCardDetailResponse(
                card=card,
                profile=session.persona_service.profile,
                soul_md=session.persona_service.soul_markdown,
                core_memory=session.memory_service.core_memory,
            )
        try:
            workspace = registry.workspace_for(persona_key)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        detail_persona_service = _build_persona_service_for_workspace(workspace)
        detail_memory_service = _build_memory_service_for_workspace(workspace)
        detail_memory_service.bind_model_runtime(
            model_client=model_client,
            model_router=model_router,
        )
        detail_persona_service.bind_model_runtime(
            model_client=model_client,
            model_router=model_router,
        )
        return PersonaCardDetailResponse(
            card=card,
            profile=detail_persona_service.profile,
            soul_md=detail_persona_service.soul_markdown,
            core_memory=detail_memory_service.core_memory,
        )

    def _services_for_persona(persona_key: str) -> tuple[PersonaService, MemoryService, bool]:
        registry = _require_persona_registry()
        session = _current_session()
        if session.persona_key == persona_key:
            return session.persona_service, session.memory_service, True
        try:
            workspace = registry.workspace_for(persona_key)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        persona_for_card = _build_persona_service_for_workspace(workspace)
        memory_for_card = _build_memory_service_for_workspace(workspace)
        memory_for_card.bind_model_runtime(model_client=model_client, model_router=model_router)
        persona_for_card.bind_model_runtime(model_client=model_client, model_router=model_router)
        return persona_for_card, memory_for_card, False

    async def _activate_persona(persona_key: str) -> RuntimeSession:
        registry = _require_persona_registry()
        try:
            card = registry.activate(persona_key)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        current_session = _current_session()
        if current_session.persona_key == card.persona_key:
            return current_session

        was_paused = current_session.orchestrator.scheduler_paused()
        was_running = current_session.orchestrator.scheduler_running()
        await current_session.orchestrator.stop_scheduler()

        new_session = _build_runtime_session(
            workspace=registry.workspace_for(card.persona_key),
            persona_key=card.persona_key,
        )
        await mcp_provider.register_tools(new_session.orchestrator.services.execution.tool_registry)
        app.state.runtime_session = new_session
        app.state.orchestrator = new_session.orchestrator
        app.state.persona_service = new_session.persona_service
        app.state.memory_service = new_session.memory_service
        app.state.scenario_runner = new_session.scenario_runner
        _bind_model_trace_sink(new_session.memory_service)

        if app.state.runtime_started:
            if was_paused:
                await new_session.orchestrator.pause_scheduler()
            elif was_running:
                await new_session.orchestrator.start_scheduler()
        return new_session

    async def _swap_to_empty_session() -> RuntimeSession:
        current_session = _current_session()
        was_paused = current_session.orchestrator.scheduler_paused()
        was_running = current_session.orchestrator.scheduler_running()
        await current_session.orchestrator.stop_scheduler()

        new_session = _build_runtime_session(workspace=None)
        await mcp_provider.register_tools(new_session.orchestrator.services.execution.tool_registry)
        app.state.runtime_session = new_session
        app.state.orchestrator = new_session.orchestrator
        app.state.persona_service = new_session.persona_service
        app.state.memory_service = new_session.memory_service
        app.state.scenario_runner = new_session.scenario_runner
        _bind_model_trace_sink(new_session.memory_service)

        if app.state.runtime_started:
            if was_paused:
                await new_session.orchestrator.pause_scheduler()
            elif was_running:
                await new_session.orchestrator.start_scheduler()
        return new_session

    async def _ensure_legacy_active_persona(name: str) -> RuntimeSession:
        session = _current_session()
        if session.persona_key is not None or persona_registry is None:
            return session
        card = persona_registry.create_card(name=name, make_active=True)
        return await _activate_persona(card.persona_key)

    @app.get("/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        orchestrator = _current_orchestrator()
        scheduler_running = orchestrator.scheduler_running()
        scheduler_paused = orchestrator.scheduler_paused()
        status = "ok"
        if scheduler_paused:
            status = "paused"
        elif orchestrator.supports_background_scheduler() and not scheduler_running:
            status = "degraded"
        mcp_servers = _mcp_server_snapshots(mcp_provider)
        return HealthResponse(
            status=status,
            scheduler_running=scheduler_running,
            scheduler_paused=scheduler_paused,
            started_at=orchestrator.started_at().isoformat(),
            mcp_configured_server_count=mcp_provider.configured_server_count(),
            mcp_connected_server_count=mcp_provider.connected_server_count(),
            mcp_registered_tool_count=mcp_provider.registered_tool_count(),
            mcp_servers=mcp_servers,
        )

    def _front_page_response(page_name: str) -> HTMLResponse:
        return HTMLResponse((front_root / "pages" / page_name).read_text(encoding="utf-8"))

    def _executor_lab_page_response() -> HTMLResponse:
        return _front_page_response("executor-lab-standalone.html")

    @app.get("/", response_class=HTMLResponse)
    async def front_index() -> HTMLResponse:
        return _front_page_response(default_front_page)

    @app.get("/front/executor-lab", response_class=HTMLResponse)
    async def front_executor_lab_page() -> HTMLResponse:
        return _executor_lab_page_response()

    @app.get("/front/workspace", response_class=HTMLResponse)
    async def front_workspace_page() -> HTMLResponse:
        return _front_page_response("workspace.html")

    @app.get("/front/debug", response_class=HTMLResponse)
    async def front_debug_page() -> HTMLResponse:
        return _executor_lab_page_response()

    @app.get("/api/prompts", response_model=list[PromptFileSummaryResponse])
    async def list_prompt_files() -> list[PromptFileSummaryResponse]:
        return [
            PromptFileSummaryResponse(
                path=record.path,
                title=record.title,
                updated_at=record.updated_at,
            )
            for record in prompt_store.list_files()
        ]

    @app.get("/api/prompts/file", response_model=PromptFileContentResponse)
    async def get_prompt_file(path: str = Query(min_length=1)) -> PromptFileContentResponse:
        try:
            content = prompt_store.read(path)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return PromptFileContentResponse(path=path, content=content)

    @app.put("/api/prompts/file", response_model=PromptFileContentResponse)
    async def update_prompt_file(
        request: PromptFileUpdateRequest,
        path: str = Query(min_length=1),
    ) -> PromptFileContentResponse:
        try:
            content = prompt_store.write(path, request.content)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return PromptFileContentResponse(path=path, content=content)

    @app.get("/api/personas", response_model=PersonaCardListResponse)
    async def list_personas() -> PersonaCardListResponse:
        registry = _require_persona_registry()
        return PersonaCardListResponse(
            active_persona_key=registry.active_persona_key(),
            cards=registry.list_cards(),
        )

    @app.delete("/api/personas/{persona_key}", response_model=PersonaCardListResponse)
    async def delete_persona_card(persona_key: str) -> PersonaCardListResponse:
        registry = _require_persona_registry()
        try:
            document = registry.delete_card(persona_key)
        except ValueError as exc:
            status_code = 404 if "Unknown persona_key" in str(exc) else 400
            raise HTTPException(status_code=status_code, detail=str(exc)) from exc

        current_session = _current_session()
        if current_session.persona_key == persona_key:
            if document.active_persona_key is None:
                await _swap_to_empty_session()
            else:
                await _activate_persona(document.active_persona_key)

        return PersonaCardListResponse(
            active_persona_key=document.active_persona_key,
            cards=document.cards,
        )

    @app.post("/api/personas", response_model=PersonaCardDetailResponse)
    async def create_persona_card(request: PersonaCardCreateRequest) -> PersonaCardDetailResponse:
        registry = _require_persona_registry()
        try:
            card = registry.create_card(
                name=request.name,
                make_active=request.activate,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if request.activate:
            await _activate_persona(card.persona_key)
        return _build_card_detail(card.persona_key)

    @app.get("/api/personas/{persona_key}", response_model=PersonaCardDetailResponse)
    async def get_persona_card(persona_key: str) -> PersonaCardDetailResponse:
        return _build_card_detail(persona_key)

    @app.put("/api/personas/{persona_key}", response_model=PersonaCardDetailResponse)
    async def update_persona_card(
        persona_key: str,
        request: PersonaCardUpdateRequest,
    ) -> PersonaCardDetailResponse:
        registry = _require_persona_registry()
        persona_for_card, memory_for_card, is_current = _services_for_persona(persona_key)
        try:
            card = registry.rename_card(persona_key, request.name)
            if persona_for_card.profile is not None:
                profile = persona_for_card.rename(request.name)
                card = registry.update_card_from_profile(persona_key, profile)
                if is_current:
                    _sync_session_persona_context(_current_session(), overwrite=True)
                    await _current_memory_service().save_snapshot(_current_orchestrator().state)
                else:
                    _sync_persona_memory_context(memory_for_card, persona_for_card)
        except ValueError as exc:
            status_code = 404 if "Unknown persona_key" in str(exc) else 400
            raise HTTPException(status_code=status_code, detail=str(exc)) from exc
        return _build_card_detail(card.persona_key)

    @app.put("/api/personas/{persona_key}/soul", response_model=PersonaCardDetailResponse)
    async def update_persona_soul(
        persona_key: str,
        request: PersonaSoulUpdateRequest,
    ) -> PersonaCardDetailResponse:
        persona_for_card, memory_for_card, is_current = _services_for_persona(persona_key)
        try:
            profile = persona_for_card.replace_soul_markdown(request.soul_md)
        except ValueError as exc:
            status_code = 404 if str(exc) == "Persona not initialized." else 400
            raise HTTPException(status_code=status_code, detail=str(exc)) from exc
        _sync_registry_card(persona_key, profile)
        if is_current:
            _sync_session_persona_context(_current_session(), overwrite=True)
            await _current_memory_service().save_snapshot(_current_orchestrator().state)
        else:
            _sync_persona_memory_context(memory_for_card, persona_for_card)
        return _build_card_detail(persona_key)

    @app.post("/api/personas/{persona_key}/bootstrap", response_model=PersonaCardDetailResponse)
    async def bootstrap_persona_card(
        persona_key: str,
        request: PersonaBootstrapRequest,
    ) -> PersonaCardDetailResponse:
        persona_for_card, memory_for_card, is_current = _services_for_persona(persona_key)
        profile = await persona_for_card.bootstrap_from_text(
            request.seed_text,
            name=request.name,
        )
        _sync_registry_card(persona_key, profile)
        if is_current:
            _sync_session_persona_context(_current_session(), overwrite=True)
            await _current_memory_service().save_snapshot(_current_orchestrator().state)
        else:
            _sync_persona_memory_context(memory_for_card, persona_for_card)
        return _build_card_detail(persona_key)

    @app.post("/api/personas/{persona_key}/activate", response_model=PersonaActivationResponse)
    async def activate_persona_card(persona_key: str) -> PersonaActivationResponse:
        registry = _require_persona_registry()
        session = await _activate_persona(persona_key)
        card = registry.get_card(persona_key)
        if card is None:
            raise HTTPException(status_code=404, detail="Persona card not found.")
        return PersonaActivationResponse(
            card=card,
            state=build_runtime_snapshot(
                state=session.orchestrator.state,
                orchestrator=session.orchestrator,
                mcp_provider=mcp_provider,
            ),
            core_memory=session.memory_service.core_memory,
        )

    @app.post("/api/persona/bootstrap", response_model=PersonaInspectionResponse)
    async def bootstrap_persona(request: PersonaBootstrapRequest) -> PersonaInspectionResponse:
        session = await _ensure_legacy_active_persona(request.name)
        profile = await session.persona_service.bootstrap_from_text(
            request.seed_text,
            name=request.name,
        )
        _sync_registry_card(session.persona_key, profile)
        _sync_session_persona_context(session, overwrite=True)
        await session.memory_service.save_snapshot(session.orchestrator.state)
        return PersonaInspectionResponse(
            profile=profile,
            core_memory=session.memory_service.core_memory,
        )

    @app.get("/api/persona", response_model=PersonaInspectionResponse)
    async def get_persona() -> PersonaInspectionResponse:
        persona_service = _current_persona_service()
        profile = persona_service.profile
        if profile is None:
            raise HTTPException(status_code=404, detail="Persona not initialized.")
        return PersonaInspectionResponse(
            profile=profile,
            core_memory=_current_memory_service().core_memory,
        )

    @app.post("/api/messages", response_model=MessageLoopResponse)
    async def post_message(request: InboundMessageRequest) -> MessageLoopResponse:
        orchestrator = _current_orchestrator()
        event = RuntimeEvent(
            event_type=EventType.MESSAGE_RECEIVED,
            source=EventSource.USER,
            payload={
                "user_id": request.user_id,
                "user_name": request.user_name or request.user_id,
                "channel": request.channel,
                "text": request.text,
            },
        )
        await orchestrator.enqueue(event, wake_background=False)
        outcome = await orchestrator.run_once()
        outbound_messages = [
            message.model_dump(mode="json") for message in communication_hub.drain_outbox()
        ]
        state = orchestrator.state

        return MessageLoopResponse(
            event_id=event.event_id,
            outcome=outcome,
            outbound_messages=outbound_messages,
            state=build_runtime_snapshot(
                state=state,
                orchestrator=orchestrator,
                mcp_provider=mcp_provider,
            ),
        )

    @app.post("/api/runtime/run-once", response_model=RuntimeLoopResponse)
    async def run_runtime_once() -> RuntimeLoopResponse:
        orchestrator = _current_orchestrator()
        outcome = await orchestrator.run_once()
        outbound_messages = [
            message.model_dump(mode="json") for message in communication_hub.drain_outbox()
        ]
        state = orchestrator.state
        return RuntimeLoopResponse(
            outcome=outcome,
            outbound_messages=outbound_messages,
            state=build_runtime_snapshot(
                state=state,
                orchestrator=orchestrator,
                mcp_provider=mcp_provider,
            ),
        )

    @app.post("/api/runtime/lifecycle/pause", response_model=RuntimeLifecycleControlResponse)
    async def pause_runtime_scheduler() -> RuntimeLifecycleControlResponse:
        orchestrator = _current_orchestrator()
        _require_background_scheduler(orchestrator)
        await orchestrator.pause_scheduler()
        snapshot = build_runtime_snapshot(
            state=orchestrator.state,
            orchestrator=orchestrator,
            mcp_provider=mcp_provider,
        )
        return RuntimeLifecycleControlResponse(
            status="paused",
            scheduler_running=orchestrator.scheduler_running(),
            scheduler_paused=orchestrator.scheduler_paused(),
            state=snapshot,
        )

    @app.post("/api/runtime/lifecycle/resume", response_model=RuntimeLifecycleControlResponse)
    async def resume_runtime_scheduler() -> RuntimeLifecycleControlResponse:
        orchestrator = _current_orchestrator()
        _require_background_scheduler(orchestrator)
        await orchestrator.resume_scheduler()
        snapshot = build_runtime_snapshot(
            state=orchestrator.state,
            orchestrator=orchestrator,
            mcp_provider=mcp_provider,
        )
        return RuntimeLifecycleControlResponse(
            status="ok",
            scheduler_running=orchestrator.scheduler_running(),
            scheduler_paused=orchestrator.scheduler_paused(),
            state=snapshot,
        )

    @app.post("/api/runtime/clock/set", response_model=RuntimeClockControlResponse)
    async def set_runtime_clock(request: RuntimeClockSetRequest) -> RuntimeClockControlResponse:
        orchestrator = _current_orchestrator()
        scenario_runner = _current_scenario_runner()
        _require_controllable_clock(orchestrator)
        trace = [await scenario_runner.set_clock(request.at, label="api:set_clock")]
        return RuntimeClockControlResponse(
            trace=trace,
            state=build_runtime_snapshot(
                state=orchestrator.state,
                orchestrator=orchestrator,
                mcp_provider=mcp_provider,
            ),
        )

    @app.post("/api/runtime/clock/pause", response_model=RuntimeLifecycleControlResponse)
    async def pause_runtime_clock() -> RuntimeLifecycleControlResponse:
        orchestrator = _current_orchestrator()
        _require_controllable_clock(orchestrator)
        _pause_runtime_clock(orchestrator)
        await orchestrator.pause_scheduler()
        snapshot = build_runtime_snapshot(
            state=orchestrator.state,
            orchestrator=orchestrator,
            mcp_provider=mcp_provider,
        )
        return RuntimeLifecycleControlResponse(
            status="paused",
            scheduler_running=orchestrator.scheduler_running(),
            scheduler_paused=orchestrator.scheduler_paused(),
            state=snapshot,
        )

    @app.post("/api/runtime/clock/resume", response_model=RuntimeLifecycleControlResponse)
    async def resume_runtime_clock() -> RuntimeLifecycleControlResponse:
        orchestrator = _current_orchestrator()
        _require_controllable_clock(orchestrator)
        _resume_runtime_clock(orchestrator)
        await orchestrator.resume_scheduler()
        snapshot = build_runtime_snapshot(
            state=orchestrator.state,
            orchestrator=orchestrator,
            mcp_provider=mcp_provider,
        )
        return RuntimeLifecycleControlResponse(
            status="ok",
            scheduler_running=orchestrator.scheduler_running(),
            scheduler_paused=orchestrator.scheduler_paused(),
            state=snapshot,
        )

    @app.post("/api/runtime/clock/advance", response_model=RuntimeClockControlResponse)
    async def advance_runtime_clock(
        request: RuntimeClockAdvanceRequest,
    ) -> RuntimeClockControlResponse:
        orchestrator = _current_orchestrator()
        scenario_runner = _current_scenario_runner()
        _require_controllable_clock(orchestrator)
        trace = [
            await scenario_runner.advance_clock(
                minutes=request.minutes,
                label="api:advance_clock",
            )
        ]
        try:
            if request.run_until_idle:
                trace.append(
                    await scenario_runner.run_until_idle(
                        max_iterations=request.max_iterations,
                        label="api:run_until_idle",
                    )
                )
            elif request.run_once:
                trace.append(await scenario_runner.run_once(label="api:run_once"))
        except RuntimeError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return RuntimeClockControlResponse(
            trace=trace,
            state=build_runtime_snapshot(
                state=orchestrator.state,
                orchestrator=orchestrator,
                mcp_provider=mcp_provider,
            ),
        )

    @app.post("/api/runtime/scenario/run", response_model=ScenarioReplayResult)
    async def replay_runtime_scenario(
        request: ScenarioReplayRequest,
    ) -> ScenarioReplayResult:
        orchestrator = _current_orchestrator()
        scenario_runner = _current_scenario_runner()
        _require_controllable_clock(orchestrator)
        try:
            return await scenario_runner.replay(request.steps)
        except RuntimeError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/runtime/plan/refresh", response_model=RuntimePlanRefreshResponse)
    async def refresh_runtime_plan() -> RuntimePlanRefreshResponse:
        orchestrator = _current_orchestrator()
        refresh_event = await orchestrator.refresh_plan()
        return RuntimePlanRefreshResponse(
            event_id=refresh_event.event_id,
            state=build_runtime_snapshot(
                state=orchestrator.state,
                orchestrator=orchestrator,
                mcp_provider=mcp_provider,
            ),
            current_plan=orchestrator.state.plan,
        )

    @app.get("/api/runtime/state", response_model=RuntimeStateInspectionResponse)
    async def get_runtime_state() -> RuntimeStateInspectionResponse:
        orchestrator = _current_orchestrator()
        memory_service = _current_memory_service()
        latest_snapshot = memory_service.latest_snapshot()
        next_step = orchestrator.next_pending_step()
        return RuntimeStateInspectionResponse(
            state=orchestrator.state,
            latest_snapshot_id=latest_snapshot.snapshot_id if latest_snapshot else None,
            latest_snapshot_at=latest_snapshot.created_at if latest_snapshot else None,
            next_wake_at=orchestrator.next_wake_at().isoformat(),
            next_step_id=next_step.step_id if next_step else None,
            next_step_scheduled_for=next_step.scheduled_for if next_step else None,
            summary=build_runtime_snapshot(
                state=orchestrator.state,
                orchestrator=orchestrator,
                mcp_provider=mcp_provider,
            ),
        )

    @app.get("/api/runtime/debug", response_model=RuntimeDebugResponse)
    async def get_runtime_debug() -> RuntimeDebugResponse:
        orchestrator = _current_orchestrator()
        memory_service = _current_memory_service()
        summary = build_runtime_snapshot(
            state=orchestrator.state,
            orchestrator=orchestrator,
            mcp_provider=mcp_provider,
        )
        latest_execution = _latest_execution_debug(memory_service)
        latest_replan = _latest_replan_debug(memory_service)
        latest_failed_execution = None
        if (
            latest_execution is not None
            and latest_execution.outcome.status != OutcomeStatus.SUCCESS
        ):
            latest_failed_execution = latest_execution
        return RuntimeDebugResponse(
            summary=summary,
            current_plan=orchestrator.state.plan,
            controls=RuntimeDebugControlsResponse(
                supports_run_once=True,
                supports_pause_resume=orchestrator.supports_background_scheduler(),
                supports_clock_control=orchestrator.clock_is_controllable(),
            ),
            latest_execution=latest_execution,
            latest_replan=latest_replan,
            latest_error=RuntimeDebugErrorResponse(
                message=orchestrator.state.last_error,
                latest_failed_execution=latest_failed_execution,
            ),
            tools=_tool_specs(orchestrator),
            mcp_servers=summary.mcp_servers,
        )

    @app.get("/api/workspace/workbench", response_model=WorkspaceWorkbenchResponse)
    async def get_workspace_workbench() -> WorkspaceWorkbenchResponse:
        orchestrator = _current_orchestrator()
        memory_service = _current_memory_service()
        summary = build_runtime_snapshot(
            state=orchestrator.state,
            orchestrator=orchestrator,
            mcp_provider=mcp_provider,
        )
        execution_records = _workspace_execution_records(memory_service)
        roleplay_context_preview = ""
        getter = getattr(memory_service, "get_persisted_roleplay_agent_context", None)
        if callable(getter):
            context = getter()
            renderer = getattr(context, "render_for_roleplay", None)
            if callable(renderer):
                roleplay_context_preview = renderer()
        return WorkspaceWorkbenchResponse(
            summary=summary,
            state=orchestrator.state,
            current_plan=orchestrator.state.plan,
            persona_name=orchestrator.state.persona_name,
            latest_execution=_latest_execution_debug(memory_service),
            latest_replan=_latest_replan_debug(memory_service),
            plan_items=_workspace_plan_items(
                state=orchestrator.state,
                execution_records=execution_records,
            ),
            roleplay_context_preview=roleplay_context_preview,
        )

    @app.get("/api/workspace/chat", response_model=WorkspaceChatResponse)
    async def get_workspace_chat(
        limit: int = Query(default=80, ge=1, le=400),
    ) -> WorkspaceChatResponse:
        orchestrator = _current_orchestrator()
        memory_service = _current_memory_service()
        roleplay_context_preview = ""
        getter = getattr(memory_service, "get_persisted_roleplay_agent_context", None)
        if callable(getter):
            context = getter()
            renderer = getattr(context, "render_for_roleplay", None)
            if callable(renderer):
                roleplay_context_preview = renderer()
        return WorkspaceChatResponse(
            persona_name=orchestrator.state.persona_name,
            entries=_workspace_chat_entries(
                memory_service,
                persona_name=orchestrator.state.persona_name,
                limit=limit,
            ),
            roleplay_context_preview=roleplay_context_preview,
        )

    @app.get("/api/plan-lab/debug", response_model=PlanLabDebugResponse)
    async def get_plan_lab_debug(
        limit: int = Query(default=12, ge=1, le=100),
    ) -> PlanLabDebugResponse:
        orchestrator = _current_orchestrator()
        memory_service = _current_memory_service()
        summary = build_runtime_snapshot(
            state=orchestrator.state,
            orchestrator=orchestrator,
            mcp_provider=mcp_provider,
        )
        return PlanLabDebugResponse(
            summary=summary,
            current_plan=orchestrator.state.plan,
            core_memory=memory_service.core_memory,
            latest_execution=_latest_execution_debug(memory_service),
            latest_replan=_latest_replan_debug(memory_service),
            planning_entries=_recent_raw_entries_by_kind(
                memory_service,
                kind="planning",
                limit=limit,
            ),
            replan_entries=_recent_raw_entries_by_kind(
                memory_service,
                kind="replan",
                limit=limit,
            ),
            model_entries=_recent_raw_entries_by_kind(
                memory_service,
                kind="model_io",
                limit=limit,
            ),
        )

    @app.post("/api/plan-lab/day-start", response_model=RuntimePlanRefreshResponse)
    async def trigger_plan_lab_day_start(
        request: PlanLabDayStartRequest,
    ) -> RuntimePlanRefreshResponse:
        orchestrator = _current_orchestrator()
        memory_service = _current_memory_service()
        _apply_plan_lab_manual_context(
            orchestrator=orchestrator,
            memory_service=memory_service,
            persona_name=request.persona_name,
            soul_md=request.soul_md,
            memories=request.memories,
        )
        now = orchestrator.now()
        payload = {"note": request.note.strip()} if request.note.strip() else {}
        event = RuntimeEvent(
            event_type=EventType.DAY_START,
            source=EventSource.RUNTIME,
            created_at=now.isoformat(),
            payload=payload,
        )
        memory_service.record_runtime_event(event)
        plan = await orchestrator.services.planning.plan_next_window(
            orchestrator.state,
            event,
            now=now,
        )
        orchestrator.state.plan = plan
        memory_service.update_plan_context(
            day_blocks=plan.day_blocks,
            plan_date=plan.plan_date,
        )
        await memory_service.save_snapshot(orchestrator.state)
        return RuntimePlanRefreshResponse(
            event_id=event.event_id,
            state=build_runtime_snapshot(
                state=orchestrator.state,
                orchestrator=orchestrator,
                mcp_provider=mcp_provider,
            ),
            current_plan=orchestrator.state.plan,
        )

    @app.post("/api/plan-lab/replan/decide", response_model=PlanLabReplanDecisionResponse)
    async def decide_plan_lab_replan(
        request: PlanLabReplanDecisionRequest,
    ) -> PlanLabReplanDecisionResponse:
        orchestrator = _current_orchestrator()
        memory_service = _current_memory_service()
        _apply_plan_lab_manual_context(
            orchestrator=orchestrator,
            memory_service=memory_service,
            persona_name=request.persona_name,
            soul_md=request.soul_md,
            memories=request.memories,
        )
        now = orchestrator.now()
        event_payload: dict[str, object] = {}
        if request.event_text.strip():
            event_payload["text"] = request.event_text.strip()
        event = RuntimeEvent(
            event_type=request.event_type,
            source=EventSource.RUNTIME,
            created_at=now.isoformat(),
            payload=event_payload,
        )
        outcome = ActionOutcome(
            action_id=orchestrator.state.current_action_id or "plan_lab_action",
            status=OutcomeStatus.SUCCESS,
            mode=request.execution_mode,
            source=request.execution_zone,
            content=request.outcome_content,
        )
        decision = await orchestrator.services.replan.decide(
            now=now,
            state=orchestrator.state,
            event=event,
            outcome=outcome,
        )
        recorder = getattr(memory_service, "record_replan_decision", None)
        if callable(recorder):
            recorder(
                decision,
                event=event,
                outcome=outcome,
            )
        return PlanLabReplanDecisionResponse(
            decision=decision,
            latest_replan=_latest_replan_debug(memory_service),
        )

    @app.post("/api/plan-lab/replan/apply", response_model=RuntimePlanRefreshResponse)
    async def apply_plan_lab_replan(
        request: PlanLabApplyReplanRequest,
    ) -> RuntimePlanRefreshResponse:
        orchestrator = _current_orchestrator()
        memory_service = _current_memory_service()
        _apply_plan_lab_manual_context(
            orchestrator=orchestrator,
            memory_service=memory_service,
            persona_name=request.persona_name,
            soul_md=request.soul_md,
            memories=request.memories,
        )
        now = orchestrator.now()
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
            action_id=orchestrator.state.current_action_id or "plan_lab_action",
            status=OutcomeStatus.SUCCESS,
            mode=request.execution_mode,
            source=request.execution_zone,
            content=request.outcome_content,
        )
        memory_service.record_runtime_event(event)
        recorder = getattr(memory_service, "record_replan_decision", None)
        if callable(recorder):
            recorder(
                ReplanDecision(kind=request.kind, reason=request.reason.strip()),
                event=event,
                outcome=outcome,
            )
        refreshed = await orchestrator.services.planning.replan_after_completion(
            orchestrator.state,
            now=now,
            kind=request.kind,
            reason=request.reason.strip(),
            event=event,
            outcome=outcome,
        )
        orchestrator.state.plan = refreshed
        memory_service.update_plan_context(
            day_blocks=refreshed.day_blocks,
            plan_date=refreshed.plan_date,
        )
        await memory_service.save_snapshot(orchestrator.state)
        return RuntimePlanRefreshResponse(
            event_id=event.event_id,
            state=build_runtime_snapshot(
                state=orchestrator.state,
                orchestrator=orchestrator,
                mcp_provider=mcp_provider,
            ),
            current_plan=orchestrator.state.plan,
        )

    @app.get("/api/models/debug", response_model=ModelTraceDebugResponse)
    async def get_model_debug(
        limit: int = Query(default=10, ge=1, le=100),
    ) -> ModelTraceDebugResponse:
        memory_service = _current_memory_service()
        recent_traces = _recent_model_traces(memory_service, limit=limit)
        return ModelTraceDebugResponse(
            recent_traces=recent_traces,
            latest_trace=recent_traces[0] if recent_traces else None,
        )

    def _current_model_config() -> ModelConfigResponse:
        return ModelConfigResponse(
            dialogue=_model_route_config(routing_settings.dialogue),
            executor=_model_route_config(routing_settings.executor),
            decision=_model_route_config(routing_settings.decision),
            memory=_model_route_config(routing_settings.memory),
            env_path=str(project_env_path()),
        )

    @app.get("/api/models/config", response_model=ModelConfigResponse)
    async def get_model_config() -> ModelConfigResponse:
        return _current_model_config()

    @app.put("/api/models/config", response_model=ModelConfigResponse)
    async def update_model_config(request: ModelConfigUpdateRequest) -> ModelConfigResponse:
        env_values = _model_route_env_values(request)
        update_project_env(env_values)
        sync_process_env(env_values)
        _apply_model_route_config(routing_settings.dialogue, request.dialogue)
        _apply_model_route_config(routing_settings.executor, request.executor)
        _apply_model_route_config(routing_settings.decision, request.decision)
        _apply_model_route_config(routing_settings.memory, request.memory)
        return _current_model_config()

    @app.post("/api/models/test", response_model=ModelConnectionTestResponse)
    async def test_model_connection(
        request: ModelConnectionTestRequest,
    ) -> ModelConnectionTestResponse:
        route = ModelRoute(
            provider=request.route.provider.strip(),
            model=request.route.model.strip(),
            api_key=request.route.api_key.strip(),
            base_url=request.route.base_url.strip(),
        )
        route_config = _model_route_config(route)
        if not route.is_configured():
            return ModelConnectionTestResponse(
                ok=False,
                role=request.role,
                route=route_config,
                error=(
                    "Route is not configured. Please provide at least "
                    "provider/model/base_url as needed."
                ),
            )
        try:
            result = await model_client.generate_text(
                ModelRequest(
                    role=request.role,
                    route=route,
                    prompt="Reply with OK only.",
                    system_prompt="This is a connectivity test. Return OK only.",
                    max_tokens=12,
                )
            )
        except Exception as exc:
            return ModelConnectionTestResponse(
                ok=False,
                role=request.role,
                route=route_config,
                error=str(exc),
            )
        return ModelConnectionTestResponse(
            ok=True,
            role=request.role,
            route=route_config,
            provider_name=result.provider_name,
            response_text=result.text,
        )

    @app.get("/api/memory", response_model=MemoryInspectionResponse)
    async def get_memory(
        limit: int = Query(default=10, ge=1, le=100),
    ) -> MemoryInspectionResponse:
        memory_service = _current_memory_service()
        return _memory_inspection_response(memory_service, limit=limit)

    @app.post("/api/memory/core/reset", response_model=MemoryInspectionResponse)
    async def reset_core_memory(
        limit: int = Query(default=10, ge=1, le=100),
    ) -> MemoryInspectionResponse:
        memory_service = _current_memory_service()
        persona_service = _current_persona_service()
        _reset_core_memory_to_soul(memory_service, persona_service)
        return _memory_inspection_response(memory_service, limit=limit)

    @app.get("/api/memory/search", response_model=MemorySearchResponse)
    async def search_memory(
        query: str = Query(min_length=1),
        top_k: int = Query(default=5, ge=1, le=20),
    ) -> MemorySearchResponse:
        memory_service = _current_memory_service()
        active_hits, archive_hits = await memory_service.search_memory(
            query_text=query,
            top_k=top_k,
        )
        return MemorySearchResponse(
            query=query,
            active_hits=active_hits,
            archive_hits=archive_hits,
        )

    @app.get("/api/memory/debug/search", response_model=MemorySearchDebugResponse)
    async def search_memory_debug(
        query: str = Query(min_length=1),
        top_k: int = Query(default=5, ge=1, le=20),
    ) -> MemorySearchDebugResponse:
        memory_service = _current_memory_service()
        payload = await memory_service.search_memory_debug(
            query_text=query,
            top_k=top_k,
        )
        active_payload = payload.get("active", {}) if isinstance(payload, dict) else {}
        archive_payload = payload.get("archive", {}) if isinstance(payload, dict) else {}
        return MemorySearchDebugResponse(
            query=query,
            active=_memory_debug_bucket(active_payload),
            archive=_memory_debug_bucket(archive_payload),
        )

    @app.get("/api/tools/debug", response_model=ToolDebugResponse)
    async def get_tool_debug(
        limit: int = Query(default=20, ge=1, le=100),
    ) -> ToolDebugResponse:
        orchestrator = _current_orchestrator()
        memory_service = _current_memory_service()
        return ToolDebugResponse(
            tools=_tool_specs(orchestrator),
            recent_invocations=_recent_tool_invocations(
                memory_service,
                orchestrator=orchestrator,
                limit=limit,
            ),
            mcp_servers=_mcp_server_snapshots(mcp_provider),
        )

    def _build_executor_lab_runner() -> ExecutorLabRunner:
        session = _current_session()
        return ExecutorLabRunner(
            execution_service=session.orchestrator.services.execution,
            memory_service=session.memory_service,
            state=session.orchestrator.state,
            now_provider=session.orchestrator.now,
        )

    @app.get("/api/executor-lab/defaults", response_model=ExecutorLabDefaultsResponse)
    async def get_standalone_executor_lab_defaults() -> ExecutorLabDefaultsResponse:
        orchestrator = _current_orchestrator()
        return empty_executor_lab_defaults(tool_specs=_tool_specs(orchestrator))

    @app.get("/api/front/executor-lab/defaults", response_model=ExecutorLabDefaultsResponse)
    async def get_executor_lab_defaults() -> ExecutorLabDefaultsResponse:
        return await get_standalone_executor_lab_defaults()

    @app.post("/api/executor-lab/run/stream")
    async def stream_executor_lab(request: ExecutorLabRequest) -> StreamingResponse:
        runner = _build_executor_lab_runner()

        async def event_stream():
            async for event in runner.stream(request):
                yield json.dumps(event.model_dump(mode="json"), ensure_ascii=False) + "\n"

        headers = {
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        }
        return StreamingResponse(
            event_stream(),
            media_type="application/x-ndjson",
            headers=headers,
        )

    @app.post("/api/executor-lab/run", response_model=ExecutorLabResponse)
    async def run_standalone_executor_lab(request: ExecutorLabRequest) -> ExecutorLabResponse:
        runner = _build_executor_lab_runner()
        try:
            return await runner.run(request)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/front/executor-lab/run", response_model=ExecutorLabResponse)
    async def run_executor_lab(request: ExecutorLabRequest) -> ExecutorLabResponse:
        return await run_standalone_executor_lab(request)

    return app


app = create_app()


def _require_controllable_clock(orchestrator: RuntimeOrchestrator) -> None:
    if not orchestrator.clock_is_controllable():
        raise HTTPException(status_code=409, detail="Runtime clock is not controllable.")


def _require_background_scheduler(orchestrator: RuntimeOrchestrator) -> None:
    if not orchestrator.supports_background_scheduler():
        raise HTTPException(status_code=409, detail="Background scheduler is not supported.")


def _pause_runtime_clock(orchestrator: RuntimeOrchestrator) -> None:
    clock = orchestrator.clock
    pauser = getattr(clock, "pause", None)
    if not callable(pauser):
        raise HTTPException(status_code=409, detail="Runtime clock does not support pause.")
    pauser()


def _resume_runtime_clock(orchestrator: RuntimeOrchestrator) -> None:
    clock = orchestrator.clock
    resumer = getattr(clock, "resume", None)
    if not callable(resumer):
        raise HTTPException(status_code=409, detail="Runtime clock does not support resume.")
    resumer()
