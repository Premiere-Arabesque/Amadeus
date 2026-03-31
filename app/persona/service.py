from __future__ import annotations

from pathlib import Path

from app.infra.model_client import ModelClient, ModelRouter
from app.infra.storage import TextFileStore
from app.persona.models import PersonaProfile
from app.prompts.store import PromptStore


class PersonaService:
    def __init__(
        self,
        profile_path: Path | None = None,
        soul_path: Path | None = None,
        *,
        model_client: ModelClient | None = None,
        model_router: ModelRouter | None = None,
        prompt_store: PromptStore | None = None,
    ) -> None:
        del profile_path
        self.soul_store = TextFileStore(soul_path or Path("memory/soul.md"))
        self.model_client = model_client
        self.model_router = model_router
        self.prompt_store = prompt_store or PromptStore()
        self._profile = self._load_profile()

    @property
    def profile(self) -> PersonaProfile | None:
        if self._profile is None:
            return None
        return self._profile.model_copy(deep=True)

    @property
    def soul_markdown(self) -> str:
        payload = self.soul_store.read()
        if payload:
            return payload
        if self._profile is None:
            return ""
        return self._render_default_soul(self._profile.name)

    @property
    def summary(self) -> str:
        name = self._profile.name if self._profile is not None else "Amadeus"
        return self._summarize_soul(self.soul_markdown, fallback_name=name)

    def bind_model_runtime(
        self,
        *,
        model_client: ModelClient | None,
        model_router: ModelRouter | None,
    ) -> None:
        self.model_client = model_client
        self.model_router = model_router

    def replace_soul_markdown(self, payload: str) -> PersonaProfile:
        cleaned = payload.strip()
        if not cleaned:
            raise ValueError("Soul markdown is required.")
        fallback_name = self._profile.name if self._profile is not None else "Amadeus"
        profile = self._parse_soul(cleaned, fallback_name=fallback_name)
        normalized = self._ensure_soul_title(cleaned, profile.name)
        self._persist_profile(profile, normalized)
        return profile

    def rename(self, name: str) -> PersonaProfile:
        if self._profile is None:
            raise ValueError("Persona not initialized.")
        cleaned = " ".join(name.split()).strip()
        if not cleaned:
            raise ValueError("Persona name is required.")
        updated_profile = PersonaProfile(name=cleaned)
        updated_soul = self._rename_soul(self.soul_markdown, cleaned)
        self._persist_profile(updated_profile, updated_soul)
        return updated_profile

    async def bootstrap_from_text(self, seed_text: str, name: str = "Amadeus") -> PersonaProfile:
        cleaned_name = " ".join(name.split()).strip() or "Amadeus"
        soul_md = self._bootstrap_soul(seed_text=seed_text, name=cleaned_name)
        profile = PersonaProfile(name=cleaned_name)
        self._persist_profile(profile, soul_md)
        return profile

    def _load_profile(self) -> PersonaProfile | None:
        payload = self.soul_store.read()
        if not payload:
            return None
        try:
            return self._parse_soul(payload, fallback_name="Amadeus")
        except Exception:
            return None

    def _persist_profile(self, profile: PersonaProfile, soul_md: str) -> None:
        self._profile = profile
        self.soul_store.write(soul_md)

    def _bootstrap_soul(self, *, seed_text: str, name: str) -> str:
        cleaned_seed = seed_text.strip()
        body = cleaned_seed or "尚未定义。"
        return (
            f"# 灵魂档案：{name}\n\n"
            "## 核心设定\n"
            f"{body}\n"
        )

    def _render_default_soul(self, name: str) -> str:
        return self._bootstrap_soul(seed_text="", name=name)

    def _parse_soul(self, payload: str, *, fallback_name: str) -> PersonaProfile:
        name = self._extract_name(payload) or fallback_name or "Amadeus"
        return PersonaProfile(name=name)

    def _extract_name(self, payload: str) -> str:
        for raw_line in payload.splitlines():
            line = raw_line.strip()
            if line.startswith("# 灵魂档案："):
                return line.split("：", 1)[1].strip()
            if line.startswith("# Soul:"):
                return line.split(":", 1)[1].strip()
            if line.startswith("## 名称"):
                continue
        lines = payload.splitlines()
        for index, raw_line in enumerate(lines):
            if raw_line.strip() in {"## 名称", "## Name"}:
                for candidate in lines[index + 1 :]:
                    cleaned = candidate.strip()
                    if cleaned:
                        return cleaned
        return ""

    def _ensure_soul_title(self, payload: str, name: str) -> str:
        title = f"# 灵魂档案：{name}"
        lines = payload.splitlines()
        for index, raw_line in enumerate(lines):
            stripped = raw_line.strip()
            if stripped.startswith("# 灵魂档案：") or stripped.startswith("# Soul:"):
                lines[index] = title
                return "\n".join(lines).strip() + "\n"
        return f"{title}\n\n{payload.strip()}\n"

    def _rename_soul(self, payload: str, name: str) -> str:
        if not payload.strip():
            return self._render_default_soul(name)
        return self._ensure_soul_title(payload, name)

    def _summarize_soul(self, payload: str, *, fallback_name: str) -> str:
        for raw_line in payload.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            if line.startswith("#"):
                continue
            if line.startswith("- "):
                return line[2:].strip()
            return line
        return f"{fallback_name} 的灵魂文档已建立。"
