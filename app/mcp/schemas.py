from __future__ import annotations

from pydantic import BaseModel, Field

from app.core.outcomes import OutcomeStatus
from app.core.types import JsonValue


class CapabilityDescriptor(BaseModel):
    name: str
    description: str
    required_arguments: list[str] = Field(default_factory=list)


class ActionRequest(BaseModel):
    capability: str
    arguments: dict[str, JsonValue] = Field(default_factory=dict)


class ActionResult(BaseModel):
    status: OutcomeStatus
    summary: str
    raw: dict[str, JsonValue] = Field(default_factory=dict)
