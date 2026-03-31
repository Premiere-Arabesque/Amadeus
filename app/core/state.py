from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field

from app.core.outcomes import OutcomeStatus
from app.core.types import ExecutionMode, ExecutionZone, JsonValue, new_id


class PlanStepStatus(StrEnum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETE = "complete"
    BLOCKED = "blocked"


class PlanOutlineStatus(StrEnum):
    PENDING = "pending"
    ACTIVE = "active"
    COMPLETE = "complete"


class PlanStep(BaseModel):
    step_id: str = Field(default_factory=lambda: new_id("step"))
    title: str
    detail: str
    minutes: int = 5
    execution_mode: ExecutionMode = ExecutionMode.NARRATIVE
    zone_hint: ExecutionZone = ExecutionZone.NON_REAL
    capability: str | None = None
    arguments: dict[str, JsonValue] = Field(default_factory=dict)
    scheduled_for: str | None = None
    started_at: str | None = None
    completed_at: str | None = None
    status: PlanStepStatus = PlanStepStatus.PENDING


class PlanOutlineItem(BaseModel):
    item_id: str = Field(default_factory=lambda: new_id("outline"))
    summary: str
    status: PlanOutlineStatus = PlanOutlineStatus.PENDING


class DayPlanBlock(BaseModel):
    block_id: str = Field(default_factory=lambda: new_id("block"))
    time: str
    label: str
    status: PlanOutlineStatus = PlanOutlineStatus.PENDING


class PlanState(BaseModel):
    plan_date: str | None = None
    day_summary: str = ""
    day_blocks: list[DayPlanBlock] = Field(default_factory=list)
    active_block_id: str | None = None
    day_plan_items: list[PlanOutlineItem] = Field(default_factory=list)
    active_day_item_id: str | None = None
    current_hour_summary: str = ""
    hour_plan_items: list[PlanOutlineItem] = Field(default_factory=list)
    active_hour_item_id: str | None = None
    hour_starts_at: str | None = None
    minute_steps: list[PlanStep] = Field(default_factory=list)


class RuntimeState(BaseModel):
    persona_name: str = ""
    persona_summary: str = ""
    current_action_id: str | None = None
    plan: PlanState = Field(default_factory=PlanState)
    pending_event_ids: list[str] = Field(default_factory=list)
    last_progress_at: str | None = None
    last_outcome_status: OutcomeStatus | None = None
    last_error: str | None = None
