from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field

from app.core.types import JsonValue, new_id, utc_now


class EventType(StrEnum):
    MESSAGE_RECEIVED = "message_received"
    ACTION_COMPLETED = "action_completed"
    DAY_START = "day_start"
    PLAN_REFRESH_REQUESTED = "plan_refresh_requested"
    SCHEDULE_WAKE = "schedule_wake"
    SYSTEM_BOOT = "system_boot"


class EventSource(StrEnum):
    USER = "user"
    TIMER = "timer"
    CHANNEL = "channel"
    RUNTIME = "runtime"
    MCP = "mcp"
    SYSTEM = "system"


class RuntimeEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: new_id("evt"))
    event_type: EventType
    source: EventSource
    created_at: str = Field(default_factory=lambda: utc_now().isoformat())
    correlation_id: str | None = None
    payload: dict[str, JsonValue] = Field(default_factory=dict)
