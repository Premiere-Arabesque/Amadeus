from __future__ import annotations

from enum import StrEnum

from pydantic import AliasChoices, BaseModel, ConfigDict, Field

from app.core.types import ExecutionMode, ExecutionZone, JsonValue, new_id


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


class ExecutionTraceEntry(BaseModel):
    stage: str
    content: str
    capability: str | None = None


class ActionOutcome(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    outcome_id: str = Field(default_factory=lambda: new_id("out"))
    action_id: str
    status: OutcomeStatus
    mode: ExecutionMode
    source: ExecutionZone = Field(validation_alias=AliasChoices("source", "zone"))
    content: str = Field(validation_alias=AliasChoices("content", "summary"))
    tool_invocations: list[ToolInvocation] = Field(default_factory=list)
    execution_trace: list[ExecutionTraceEntry] = Field(default_factory=list)
    raw_data: dict[str, JsonValue] = Field(
        default_factory=dict,
        validation_alias=AliasChoices("raw_data", "raw"),
    )

    @property
    def zone(self) -> ExecutionZone:
        return self.source

    @zone.setter
    def zone(self, value: ExecutionZone) -> None:
        self.source = value

    @property
    def summary(self) -> str:
        return self.content

    @summary.setter
    def summary(self, value: str) -> None:
        self.content = value

    @property
    def raw(self) -> dict[str, JsonValue]:
        return self.raw_data

    @raw.setter
    def raw(self, value: dict[str, JsonValue]) -> None:
        self.raw_data = value


class ReplanDecision(BaseModel):
    kind: ReplanKind = ReplanKind.NO_REPLAN
    reason: str = ""
    confidence: float | None = None
    source: str = ""
