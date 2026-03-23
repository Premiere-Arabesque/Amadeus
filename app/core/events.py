from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field

from app.core.types import JsonValue, new_id, utc_now


class EventType(StrEnum):
    MESSAGE_RECEIVED = "message_received"
    MINUTE_TICK = "minute_tick"
    HOUR_TICK = "hour_tick"
    ACTION_COMPLETED = "action_completed"
    TOOL_RESULT = "tool_result"
    TOOL_FAILED = "tool_failed"
    EMOTION_UPDATED = "emotion_updated"
    REPLAN_REQUESTED = "replan_requested"
    OUTBOUND_MESSAGE_REQUESTED = "outbound_message_requested"


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
