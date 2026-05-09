from __future__ import annotations

from pydantic import BaseModel, Field

from app.core.types import JsonValue, new_id, utc_now


class RawLogEntry(BaseModel):
    entry_id: str = Field(default_factory=lambda: new_id("raw"))
    created_at: str = Field(default_factory=lambda: utc_now().isoformat())
    kind: str
    source: str
    payload: dict[str, JsonValue] = Field(default_factory=dict)


class CoreMemoryExecutionRecord(BaseModel):
    recorded_at: str = Field(default_factory=lambda: utc_now().isoformat())
    step_title: str
    outcome_status: str
    source: str
    content: str


class CoreMemory(BaseModel):
    soul_md: str = ""
    stable_facts: list[str] = Field(default_factory=list)
    relationship_conclusions: list[str] = Field(default_factory=list)
    important_conclusions: list[str] = Field(default_factory=list)
    updated_at: str = Field(default_factory=lambda: utc_now().isoformat())


class ActiveMemoryEntry(BaseModel):
    entry_id: str = Field(default_factory=lambda: new_id("mem"))
    created_at: str = Field(default_factory=lambda: utc_now().isoformat())
    content: str
    source: str
    interaction_partner: str | None = None
    importance_score: float | None = None
    semantic_embedding: list[float] = Field(default_factory=list)
    emotional_embedding: list[float] = Field(default_factory=list)


class ArchiveMemoryEntry(BaseModel):
    entry_id: str = Field(default_factory=lambda: new_id("arc"))
    created_at: str = Field(default_factory=lambda: utc_now().isoformat())
    content: str
    source: str
    interaction_partner: str | None = None


class RuntimeSnapshot(BaseModel):
    snapshot_id: str = Field(default_factory=lambda: new_id("snap"))
    created_at: str = Field(default_factory=lambda: utc_now().isoformat())
    state: dict[str, JsonValue]
