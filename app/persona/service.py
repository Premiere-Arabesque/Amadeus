from __future__ import annotations

import re
from pathlib import Path

from pydantic import BaseModel, Field

from app.core.types import new_id
from app.infra.model_client import ModelClient, ModelRole, ModelRouter
from app.infra.storage import JsonFileStore
from app.persona.models import PersonaProfile


class PersonaDraft(BaseModel):
    summary: str
    stable_traits: list[str] = Field(default_factory=list)
    relationship_context: str = ""
    preferences: list[str] = Field(default_factory=list)


class PersonaService:
    def __init__(
        self,
        profile_path: Path | None = None,
        *,
        model_client: ModelClient | None = None,
        model_router: ModelRouter | None = None,
    ) -> None:
        self.store = JsonFileStore(profile_path or Path("memory/persona_profile.json"))
        self.model_client = model_client
        self.model_router = model_router
        self._profile = self._load_profile()

    @property
    def profile(self) -> PersonaProfile | None:
        if self._profile is None:
            return None
        return self._profile.model_copy(deep=True)

    def bind_model_runtime(
        self,
        *,
        model_client: ModelClient | None,
        model_router: ModelRouter | None,
    ) -> None:
        self.model_client = model_client
        self.model_router = model_router

    async def bootstrap_from_text(self, seed_text: str, name: str = "Amadeus") -> PersonaProfile:
        cleaned_seed = seed_text.strip()
        draft = await self._extract_persona(cleaned_seed, name=name)
        profile = PersonaProfile(
            persona_id=new_id("persona"),
            name=name,
            summary=draft.summary,
            background=cleaned_seed,
            stable_traits=draft.stable_traits,
            relationship_context=draft.relationship_context,
            preferences=draft.preferences,
        )
        self._profile = profile
        self.store.write(profile.model_dump(mode="json"))
        return profile

    def _load_profile(self) -> PersonaProfile | None:
        payload = self.store.read()
        if payload is None:
            return None
        return PersonaProfile.model_validate(payload)

    async def _extract_persona(self, seed_text: str, *, name: str) -> PersonaDraft:
        if not seed_text:
            return PersonaDraft(
                summary=f"{name} is awaiting a fuller persona definition.",
                relationship_context=(
                    f"{name} is still getting to know the user through future conversation."
                ),
            )

        structured = await self._extract_with_model(seed_text, name=name)
        if structured is not None:
            return structured
        return self._extract_heuristically(seed_text, name=name)

    async def _extract_with_model(
        self,
        seed_text: str,
        *,
        name: str,
    ) -> PersonaDraft | None:
        if self.model_client is None or self.model_router is None:
            return None

        route = self.model_router.resolve(ModelRole.MEMORY)
        if not route.model:
            return None

        request = self.model_router.build_request(
            ModelRole.MEMORY,
            prompt=(
                f"Character name: {name}\n"
                "Seed description:\n"
                f"{seed_text}\n\n"
                "Extract a stable persona profile for an always-on agent. "
                "Keep the summary to 2 sentences max, list 3-6 stable traits, "
                "note the current relationship context with the user, and list 2-5 preferences."
            ),
            system_prompt=(
                "You extract stable character profiles for a role simulation runtime. "
                "Prefer durable traits over temporary moods."
            ),
        )
        try:
            response = await self.model_client.generate_structured(request, PersonaDraft)
        except Exception:
            return None

        if not isinstance(response.structured, PersonaDraft):
            return None
        return response.structured

    def _extract_heuristically(self, seed_text: str, *, name: str) -> PersonaDraft:
        if not seed_text:
            return PersonaDraft(
                summary=f"{name} is awaiting a fuller persona definition.",
                relationship_context=(
                    f"{name} is still getting to know the user through future conversation."
                ),
            )

        sentences = [
            fragment.strip()
            for fragment in re.split(r"(?<=[.!?。！？])\s+", seed_text)
            if fragment.strip()
        ]
        summary = " ".join(sentences[:2]) if sentences else seed_text.strip()
        summary = summary[:280].rstrip()

        collapsed = " ".join(part.strip() for part in seed_text.splitlines() if part.strip())
        traits = self._extract_traits(collapsed)
        preferences = self._extract_preferences(collapsed)
        relationship_context = self._extract_relationship_context(collapsed, name=name)
        return PersonaDraft(
            summary=summary or collapsed[:280],
            stable_traits=traits,
            relationship_context=relationship_context,
            preferences=preferences,
        )

    def _extract_traits(self, text: str) -> list[str]:
        lowered = text.lower()
        prefix = re.split(r"\b(?:who|that|with|but|and|喜欢|偏好)\b", lowered, maxsplit=1)[0]
        prefix = re.sub(r"^(?:an?|the)\s+", "", prefix).strip(" ,.;:()")
        raw_parts = re.split(r"[,/]| and ", prefix)
        traits: list[str] = []
        for part in raw_parts:
            cleaned = " ".join(part.split()).strip(" ,.;:()")
            if not cleaned or len(cleaned) > 32:
                continue
            if cleaned not in traits:
                traits.append(cleaned)
            if len(traits) >= 5:
                break
        return traits

    def _extract_preferences(self, text: str) -> list[str]:
        patterns = [
            r"\b(?:likes|enjoys|prefers)\s+([^.;,!]+)",
            r"(?:喜欢|偏好|更喜欢)([^。；，!]+)",
        ]
        preferences: list[str] = []
        for pattern in patterns:
            for match in re.finditer(pattern, text, flags=re.IGNORECASE):
                cleaned = " ".join(match.group(1).split()).strip(" ,.;:()")
                if cleaned and cleaned not in preferences:
                    preferences.append(cleaned)
                if len(preferences) >= 5:
                    return preferences
        return preferences

    def _extract_relationship_context(self, text: str, *, name: str) -> str:
        lowered = text.lower()
        if any(token in lowered for token in ["friend", "partner", "assistant", "companion"]):
            return (
                f"{name} already has an implied relationship frame with the user and should "
                "maintain it consistently."
            )
        if any(token in text for token in ["朋友", "助手", "搭档", "恋人", "同伴"]):
            return f"{name} already has an implied relationship frame with the user."
        return f"{name} is still getting to know the user through ongoing conversation."
