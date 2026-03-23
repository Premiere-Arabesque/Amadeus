from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field

from app.core.types import ExecutionMode, JsonValue, new_id


class OutcomeStatus(StrEnum):
    SUCCESS = "success"
    PARTIAL_SUCCESS = "partial_success"
    RETRYABLE_FAILURE = "retryable_failure"
    BLOCKED_FAILURE = "blocked_failure"


class ReplanKind(StrEnum):
    NO_REPLAN = "no_replan"
    MICRO_REPLAN = "micro_replan"
    HOUR_REPLAN = "hour_replan"


class ToolInvocation(BaseModel):
    capability: str
    arguments: dict[str, JsonValue] = Field(default_factory=dict)
    status: OutcomeStatus = OutcomeStatus.SUCCESS
    detail: str = ""


class ActionOutcome(BaseModel):
    outcome_id: str = Field(default_factory=lambda: new_id("out"))
    action_id: str
    status: OutcomeStatus
    mode: ExecutionMode
    summary: str
    tool_invocations: list[ToolInvocation] = Field(default_factory=list)
    raw: dict[str, JsonValue] = Field(default_factory=dict)


class ReplanDecision(BaseModel):
    kind: ReplanKind = ReplanKind.NO_REPLAN
    reason: str = ""
