from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class JsonlFileStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, payload: dict[str, object]) -> None:
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")

    def replace_all(self, payloads: list[dict[str, object]]) -> None:
        with self.path.open("w", encoding="utf-8") as handle:
            for payload in payloads:
                handle.write(json.dumps(payload, ensure_ascii=False) + "\n")

    def read_all(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []

        rows: list[dict[str, Any]] = []
        with self.path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                rows.append(json.loads(line))
        return rows

    def read_recent(self, limit: int) -> list[dict[str, Any]]:
        if limit <= 0:
            return []
        return self.read_all()[-limit:]


class JsonFileStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, payload: dict[str, object]) -> None:
        with self.path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)

    def read(self) -> dict[str, Any] | None:
        if not self.path.exists():
            return None

        with self.path.open("r", encoding="utf-8") as handle:
            return json.load(handle)


def sqlite_url(path: str = "amadeus.sqlite3") -> str:
    return f"sqlite:///{path}"
