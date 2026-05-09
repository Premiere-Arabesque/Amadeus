from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, Field

from app.core.types import utc_now
from app.infra.storage import JsonFileStore
from app.persona.models import PersonaProfile


def _timestamp() -> str:
    return utc_now().isoformat()


class PersonaCard(BaseModel):
    persona_key: str
    name: str
    created_at: str = Field(default_factory=_timestamp)
    updated_at: str = Field(default_factory=_timestamp)


class PersonaRegistryDocument(BaseModel):
    active_persona_key: str | None = None
    cards: list[PersonaCard] = Field(default_factory=list)


@dataclass(frozen=True)
class PersonaWorkspace:
    persona_key: str
    root: Path

    @property
    def directory(self) -> Path:
        return self.root / self.persona_key

    @property
    def soul_path(self) -> Path:
        return self.directory / "soul.md"

    @property
    def core_memory_path(self) -> Path:
        return self.directory / "core_memory.json"

    @property
    def roleplay_context_path(self) -> Path:
        return self.directory / "roleplay_context.json"

    @property
    def active_memory_path(self) -> Path:
        return self.directory / "active_memory.jsonl"

    @property
    def archive_memory_path(self) -> Path:
        return self.directory / "archive_memory.jsonl"

    @property
    def snapshot_path(self) -> Path:
        return self.directory / "snapshots.jsonl"

    @property
    def raw_log_path(self) -> Path:
        return self.directory / "raw_log"


class PersonaRegistry:
    def __init__(
        self,
        *,
        index_path: Path | None = None,
        workspace_root: Path | None = None,
    ) -> None:
        self.workspace_root = workspace_root or Path("memory/personas")
        self.store = JsonFileStore(index_path or (self.workspace_root / "index.json"))
        self._document = self._load()

    def list_cards(self) -> list[PersonaCard]:
        return [card.model_copy(deep=True) for card in self._document.cards]

    def get_card(self, persona_key: str) -> PersonaCard | None:
        for card in self._document.cards:
            if card.persona_key == persona_key:
                return card.model_copy(deep=True)
        return None

    def create_card(self, name: str, *, make_active: bool = False) -> PersonaCard:
        cleaned_name = " ".join(name.split()).strip()
        if not cleaned_name:
            raise ValueError("Persona name is required.")
        if self._find_by_name(cleaned_name) is not None:
            raise ValueError(f"Persona name already exists: {cleaned_name}")

        card = PersonaCard(
            persona_key=self._next_persona_key(cleaned_name),
            name=cleaned_name,
        )
        self._document.cards.append(card)
        if make_active or self._document.active_persona_key is None:
            self._document.active_persona_key = card.persona_key
        self._persist()
        return card.model_copy(deep=True)

    def rename_card(self, persona_key: str, name: str) -> PersonaCard:
        cleaned_name = " ".join(name.split()).strip()
        if not cleaned_name:
            raise ValueError("Persona name is required.")
        existing = self._find_by_name(cleaned_name)
        if existing is not None and existing.persona_key != persona_key:
            raise ValueError(f"Persona name already exists: {cleaned_name}")

        card = self._require_card(persona_key)
        card.name = cleaned_name
        card.updated_at = _timestamp()
        self._persist()
        return card.model_copy(deep=True)

    def update_card_from_profile(
        self,
        persona_key: str,
        profile: PersonaProfile | None,
    ) -> PersonaCard:
        card = self._require_card(persona_key)
        if profile is not None:
            card.name = profile.name
        card.updated_at = _timestamp()
        self._persist()
        return card.model_copy(deep=True)

    def activate(self, persona_key: str) -> PersonaCard:
        card = self._require_card(persona_key)
        self._document.active_persona_key = card.persona_key
        card.updated_at = _timestamp()
        self._persist()
        return card.model_copy(deep=True)

    def delete_card(self, persona_key: str) -> PersonaRegistryDocument:
        card = self._require_card(persona_key)
        self._document.cards = [
            existing
            for existing in self._document.cards
            if existing.persona_key != card.persona_key
        ]
        if self._document.active_persona_key == card.persona_key:
            self._document.active_persona_key = (
                self._document.cards[0].persona_key if self._document.cards else None
            )
        self._persist()
        self._remove_workspace(card.persona_key)
        return self._document.model_copy(deep=True)

    def active_persona_key(self) -> str | None:
        return self._document.active_persona_key

    def active_card(self) -> PersonaCard | None:
        active_key = self._document.active_persona_key
        if active_key is None:
            return None
        return self.get_card(active_key)

    def workspace_for(self, persona_key: str) -> PersonaWorkspace:
        self._require_card(persona_key)
        return PersonaWorkspace(persona_key=persona_key, root=self.workspace_root)

    def active_workspace(self) -> PersonaWorkspace | None:
        active_key = self._document.active_persona_key
        if active_key is None:
            return None
        return self.workspace_for(active_key)

    def _load(self) -> PersonaRegistryDocument:
        payload = self.store.read()
        if payload is None:
            return PersonaRegistryDocument()
        return PersonaRegistryDocument.model_validate(payload)

    def _persist(self) -> None:
        self.store.write(self._document.model_dump(mode="json"))

    def _require_card(self, persona_key: str) -> PersonaCard:
        for index, card in enumerate(self._document.cards):
            if card.persona_key == persona_key:
                return self._document.cards[index]
        raise ValueError(f"Unknown persona_key: {persona_key}")

    def _find_by_name(self, name: str) -> PersonaCard | None:
        lowered = name.casefold()
        for card in self._document.cards:
            if card.name.casefold() == lowered:
                return card
        return None

    def _next_persona_key(self, name: str) -> str:
        base = _slugify(name)
        candidate = base
        suffix = 2
        while any(card.persona_key == candidate for card in self._document.cards):
            candidate = f"{base}-{suffix}"
            suffix += 1
        return candidate

    def _remove_workspace(self, persona_key: str) -> None:
        workspace_dir = (self.workspace_root / persona_key).resolve()
        root_dir = self.workspace_root.resolve()
        try:
            workspace_dir.relative_to(root_dir)
        except ValueError as exc:
            raise ValueError("Persona workspace resolved outside the workspace root.") from exc
        if workspace_dir.exists():
            shutil.rmtree(workspace_dir)


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower()).strip("-")
    return slug or "persona"
