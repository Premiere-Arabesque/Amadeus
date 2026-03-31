from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any


class JsonFileStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def read(self) -> Any:
        if not self.path.exists():
            return None
        with self.path.open("r", encoding="utf-8") as handle:
            return json.load(handle)

    def write(self, payload: Any) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)


class TextFileStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def read(self) -> str | None:
        if not self.path.exists():
            return None
        return self.path.read_text(encoding="utf-8")

    def write(self, payload: str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(payload, encoding="utf-8")


class JsonlFileStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def read_all(self) -> list[Any]:
        if not self.path.exists():
            return []
        items: list[Any] = []
        with self.path.open("r", encoding="utf-8") as handle:
            for line in handle:
                stripped = line.strip()
                if stripped:
                    items.append(json.loads(stripped))
        return items

    def read_recent(self, limit: int = 10) -> list[Any]:
        if limit <= 0:
            return []
        return self.read_all()[-limit:]

    def append(self, payload: Any) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False))
            handle.write("\n")

    def replace_all(self, payloads: list[Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("w", encoding="utf-8") as handle:
            for payload in payloads:
                handle.write(json.dumps(payload, ensure_ascii=False))
                handle.write("\n")


class DatedJsonlStore:
    def __init__(self, root: Path, *, filename: str = "entries.jsonl") -> None:
        self.root = root
        self.filename = filename

    def read_all(self) -> list[Any]:
        if not self.root.exists():
            return []
        items: list[Any] = []
        for day_dir in sorted(path for path in self.root.iterdir() if path.is_dir()):
            file_path = day_dir / self.filename
            if not file_path.exists():
                continue
            with file_path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    stripped = line.strip()
                    if stripped:
                        items.append(json.loads(stripped))
        return items

    def read_recent(self, limit: int = 10) -> list[Any]:
        if limit <= 0:
            return []
        return self.read_all()[-limit:]

    def append(self, payload: Any) -> None:
        file_path = self._file_path_for(payload)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        with file_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False))
            handle.write("\n")

    def _file_path_for(self, payload: Any) -> Path:
        folder_name = _date_folder_name(payload)
        return self.root / folder_name / self.filename


class SnapshotStore:
    def __init__(self, path: Path) -> None:
        self._jsonl = JsonlFileStore(path)

    async def append(self, payload: Any) -> None:
        self._jsonl.append(payload)

    def latest(self) -> Any:
        items = self._jsonl.read_recent(limit=1)
        if not items:
            return None
        return items[-1]

    def recent(self, limit: int = 10) -> list[Any]:
        return self._jsonl.read_recent(limit=limit)


def _date_folder_name(payload: Any) -> str:
    created_at = ""
    if isinstance(payload, dict):
        created_at = str(payload.get("created_at", "")).strip()
    try:
        if created_at:
            return datetime.fromisoformat(created_at).date().isoformat()
    except Exception:
        pass
    return datetime.now().date().isoformat()
