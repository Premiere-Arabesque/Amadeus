from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field

from app.core.outcomes import OutcomeStatus
from app.core.state import RuntimeState
from app.runtime.orchestrator import RuntimeOrchestrator


class RuntimeStatus(StrEnum):
    BOOTING = "booting"
    IDLE = "idle"
    WAITING = "waiting"
    PROCESSING = "processing"
    PAUSED = "paused"
    ERROR = "error"


class MCPServerStatusSnapshot(BaseModel):
    server_id: str
    transport: str
    connected: bool = False
    registered_tools: list[str] = Field(default_factory=list)
    tool_count: int = 0


class RuntimeStateSnapshot(BaseModel):
    current_time: str
    runtime_status: RuntimeStatus
    plan_summary: str
    active_day_summary: str | None = None
    active_hour_summary: str | None = None
    current_action_id: str | None = None
    scheduler_running: bool = False
    scheduler_paused: bool = False
    pending_event_count: int = 0
    next_wake_at: str | None = None
    next_step_id: str | None = None
    next_step_scheduled_for: str | None = None
    last_progress_at: str | None = None
    last_outcome_status: OutcomeStatus | None = None
    last_error: str | None = None
    clock_mode: str = "manual"
    pending_event_ids: list[str] = Field(default_factory=list)
    clock_controllable: bool = False
    mcp_configured_server_count: int = 0
    mcp_connected_server_count: int = 0
    mcp_registered_tool_count: int = 0
    mcp_servers: list[MCPServerStatusSnapshot] = Field(default_factory=list)


def build_runtime_snapshot(
    *,
    state: RuntimeState,
    orchestrator: RuntimeOrchestrator,
    mcp_provider: object | None = None,
) -> RuntimeStateSnapshot:
    now = orchestrator.now()
    next_step = orchestrator.next_pending_step()
    scheduler_running = orchestrator.scheduler_running()
    scheduler_paused = orchestrator.scheduler_paused()
    next_wake_at = orchestrator.next_wake_at()
    active_day_summary = next(
        (
            item.summary
            for item in state.plan.day_plan_items
            if item.item_id == state.plan.active_day_item_id
        ),
        None,
    )
    active_hour_summary = next(
        (
            item.summary
            for item in state.plan.hour_plan_items
            if item.item_id == state.plan.active_hour_item_id
        ),
        state.plan.current_hour_summary or None,
    )
    server_status = _mcp_server_status(mcp_provider)
    return RuntimeStateSnapshot(
        current_time=now.isoformat(),
        runtime_status=_runtime_status(
            state=state,
            orchestrator=orchestrator,
            now=now,
            scheduler_running=scheduler_running,
            scheduler_paused=scheduler_paused,
            next_wake_at=next_wake_at,
        ),
        plan_summary=state.plan.day_summary,
        active_day_summary=active_day_summary,
        active_hour_summary=active_hour_summary,
        current_action_id=state.current_action_id,
        scheduler_running=scheduler_running,
        scheduler_paused=scheduler_paused,
        pending_event_count=len(state.pending_event_ids),
        next_wake_at=next_wake_at.isoformat(),
        next_step_id=next_step.step_id if next_step else None,
        next_step_scheduled_for=next_step.scheduled_for if next_step else None,
        last_progress_at=state.last_progress_at,
        last_outcome_status=state.last_outcome_status,
        last_error=state.last_error,
        clock_mode=orchestrator.clock_mode(),
        pending_event_ids=state.pending_event_ids,
        clock_controllable=orchestrator.clock_is_controllable(),
        mcp_configured_server_count=len(server_status),
        mcp_connected_server_count=sum(1 for status in server_status if status.connected),
        mcp_registered_tool_count=sum(status.tool_count for status in server_status),
        mcp_servers=server_status,
    )


def _runtime_status(
    *,
    state: RuntimeState,
    orchestrator: RuntimeOrchestrator,
    now: datetime,
    scheduler_running: bool,
    scheduler_paused: bool,
    next_wake_at: datetime,
) -> RuntimeStatus:
    if orchestrator.is_processing():
        return RuntimeStatus.PROCESSING
    if scheduler_paused:
        return RuntimeStatus.PAUSED
    if state.last_error is not None:
        return RuntimeStatus.ERROR
    if (
        state.last_progress_at is None
        and state.current_action_id is None
        and not state.pending_event_ids
    ):
        return RuntimeStatus.BOOTING
    if state.pending_event_ids:
        return RuntimeStatus.IDLE
    if scheduler_running and next_wake_at > now:
        return RuntimeStatus.WAITING
    return RuntimeStatus.IDLE


def _mcp_server_status(mcp_provider: object | None) -> list[MCPServerStatusSnapshot]:
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
    normalized: list[MCPServerStatusSnapshot] = []
    for item in statuses:
        if not isinstance(item, dict):
            continue
        registered_tools = item.get("registered_tools", [])
        if not isinstance(registered_tools, list):
            registered_tools = []
        normalized.append(
            MCPServerStatusSnapshot(
                server_id=str(item.get("server_id", "")),
                transport=str(item.get("transport", "")),
                connected=bool(item.get("connected", False)),
                registered_tools=[str(name) for name in registered_tools],
                tool_count=int(item.get("tool_count", 0)),
            )
        )
    return normalized
