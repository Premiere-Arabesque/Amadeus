from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field

from app.core.types import ExecutionMode, JsonValue, new_id


class PlanStepStatus(StrEnum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETE = "complete"
    BLOCKED = "blocked"


class EmotionState(BaseModel):
    summary: str = "neutral"
    valence: float = 0.0
    arousal: float = 0.0
    dominance: float = 0.0


class PlanStep(BaseModel):
    step_id: str = Field(default_factory=lambda: new_id("step"))
    title: str
    detail: str
    minutes: int = 5
    execution_mode: ExecutionMode = ExecutionMode.NARRATIVE
    capability: str | None = None
    arguments: dict[str, JsonValue] = Field(default_factory=dict)
    scheduled_for: str | None = None
    started_at: str | None = None
    completed_at: str | None = None
    status: PlanStepStatus = PlanStepStatus.PENDING


class PlanState(BaseModel):
    day_summary: str = ""
    current_hour_summary: str = ""
    hour_starts_at: str | None = None
    minute_steps: list[PlanStep] = Field(default_factory=list)


class RuntimeState(BaseModel):
    persona_id: str | None = None
    persona_summary: str = ""
    current_action_id: str | None = None
    emotion: EmotionState = Field(default_factory=EmotionState)
    plan: PlanState = Field(default_factory=PlanState)
    pending_event_ids: list[str] = Field(default_factory=list)
