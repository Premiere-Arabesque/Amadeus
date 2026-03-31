from __future__ import annotations

from collections.abc import Awaitable, Callable
from enum import StrEnum

from pydantic import BaseModel, Field

from app.core.outcomes import OutcomeStatus
from app.core.types import JsonValue


class ToolSourceType(StrEnum):
    INTERNAL = "internal"
    MCP = "mcp"


class ToolSpec(BaseModel):
    name: str
    description: str
    required_arguments: list[str] = Field(default_factory=list)
    source_type: ToolSourceType = ToolSourceType.INTERNAL
    source_id: str = ""
    metadata: dict[str, JsonValue] = Field(default_factory=dict)


class ActionResult(BaseModel):
    status: OutcomeStatus
    summary: str
    raw: dict[str, JsonValue] = Field(default_factory=dict)


ToolExecutor = Callable[[dict[str, JsonValue]], Awaitable[ActionResult]]

# Backward-compatible alias for the previous MCP-shaped naming.
CapabilityDescriptor = ToolSpec
