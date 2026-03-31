from __future__ import annotations

from pydantic import BaseModel


class PersonaProfile(BaseModel):
    name: str = "Amadeus"
