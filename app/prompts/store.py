from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

DEFAULT_PROMPT_ROOT = Path(__file__).resolve().parent
PROMPT_SUFFIXES = {".txt", ".md", ".prompt"}


@dataclass(frozen=True)
class PromptFileRecord:
    path: str
    title: str
    updated_at: str


class PromptStore:
    def __init__(self, root: Path | None = None) -> None:
        self.root = (root or DEFAULT_PROMPT_ROOT).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def list_files(self) -> list[PromptFileRecord]:
        records: list[PromptFileRecord] = []
        for file_path in sorted(self.root.rglob("*")):
            if not file_path.is_file() or file_path.suffix.lower() not in PROMPT_SUFFIXES:
                continue
            relative_path = file_path.relative_to(self.root).as_posix()
            records.append(
                PromptFileRecord(
                    path=relative_path,
                    title=file_path.stem.replace("_", " "),
                    updated_at=datetime.fromtimestamp(
                        file_path.stat().st_mtime,
                    ).isoformat(),
                )
            )
        return records

    def list_paths(self) -> list[str]:
        return [record.path for record in self.list_files()]

    def resolve(self, relative_path: str) -> Path:
        candidate = (self.root / relative_path).resolve()
        if candidate == self.root:
            raise ValueError("Prompt path must point to a file.")
        if self.root not in candidate.parents:
            raise ValueError("Prompt path is outside the prompt root.")
        if candidate.suffix.lower() not in PROMPT_SUFFIXES:
            raise ValueError("Prompt file must use a text prompt suffix.")
        return candidate

    def read(self, relative_path: str) -> str:
        return self.resolve(relative_path).read_text(encoding="utf-8")

    def write(self, relative_path: str, content: str) -> str:
        target = self.resolve(relative_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return self.read(relative_path)

    def load(self, relative_path: str, *, default: str) -> str:
        try:
            return self.read(relative_path)
        except Exception:
            return default
