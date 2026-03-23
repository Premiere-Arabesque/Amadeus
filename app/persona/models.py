from __future__ import annotations

from pydantic import BaseModel, Field


class PersonaProfile(BaseModel):
    persona_id: str
    name: str
    summary: str
    background: str = ""
    stable_traits: list[str] = Field(default_factory=list)
    relationship_context: str = ""
    preferences: list[str] = Field(default_factory=list)
