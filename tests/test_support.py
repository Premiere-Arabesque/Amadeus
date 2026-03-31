from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from app.infra.settings import MemoryStorageSettings
from app.memory.service import MemoryService
from app.persona.service import PersonaService


class InMemoryJsonStore:
    def __init__(self, payload=None) -> None:
        self.payload = payload

    def read(self):
        return self.payload

    def write(self, payload) -> None:
        self.payload = payload


class InMemoryTextStore:
    def __init__(self, payload: str | None = None) -> None:
        self.payload = payload

    def read(self) -> str | None:
        return self.payload

    def write(self, payload: str) -> None:
        self.payload = payload


class InMemoryJsonlStore:
    def __init__(self, payloads=None) -> None:
        self.payloads = list(payloads or [])

    def read_all(self):
        return list(self.payloads)

    def read_recent(self, limit: int = 10):
        if limit <= 0:
            return []
        return self.payloads[-limit:]

    def append(self, payload) -> None:
        self.payloads.append(payload)

    def replace_all(self, payloads) -> None:
        self.payloads = list(payloads)


class InMemorySnapshotStore:
    def __init__(self) -> None:
        self.snapshots = []

    async def append(self, snapshot) -> None:
        self.snapshots.append(snapshot)

    def latest(self):
        if not self.snapshots:
            return None
        return self.snapshots[-1]

    def recent(self, limit: int = 10):
        if limit <= 0:
            return []
        return self.snapshots[-limit:]


@dataclass
class MemoryHarness:
    raw_store: InMemoryJsonlStore = field(default_factory=InMemoryJsonlStore)
    snapshot_store: InMemorySnapshotStore = field(default_factory=InMemorySnapshotStore)
    active_store: InMemoryJsonlStore = field(default_factory=InMemoryJsonlStore)
    core_store: InMemoryJsonStore = field(default_factory=InMemoryJsonStore)
    archive_store: InMemoryJsonlStore = field(default_factory=InMemoryJsonlStore)


@dataclass
class PersonaHarness:
    soul_store: InMemoryTextStore = field(default_factory=InMemoryTextStore)


def build_in_memory_memory_service(
    *,
    harness: MemoryHarness | None = None,
    active_retention_days: int = 999,
    model_client=None,
    model_router=None,
    semantic_entry_embedder=None,
    semantic_query_embedder=None,
) -> tuple[MemoryService, MemoryHarness]:
    harness = harness or MemoryHarness()
    service = MemoryService(
        raw_log_path=Path("memory/tests/raw"),
        snapshot_path=Path("memory/tests/snapshots.jsonl"),
        active_memory_path=Path("memory/tests/active.jsonl"),
        core_memory_path=Path("memory/tests/core.json"),
        archive_memory_path=Path("memory/tests/archive.jsonl"),
        storage_settings=MemoryStorageSettings(
            active_retention_days=active_retention_days,
        ),
        model_client=model_client,
        model_router=model_router,
        semantic_entry_embedder=semantic_entry_embedder,
        semantic_query_embedder=semantic_query_embedder,
    )
    service.raw_store = harness.raw_store
    service.snapshot_store = harness.snapshot_store
    service.active_store = harness.active_store
    service.core_store = harness.core_store
    service.archive_store = harness.archive_store
    service.core_memory = service._load_core_memory()
    service.raw_entries = service._load_raw_entries()
    service.active_entries = service._load_active_entries()
    service.archive_entries = service._load_archive_entries()
    return service, harness


def build_in_memory_persona_service(
    *,
    harness: PersonaHarness | None = None,
    model_client=None,
    model_router=None,
) -> tuple[PersonaService, PersonaHarness]:
    harness = harness or PersonaHarness()
    service = PersonaService(
        soul_path=Path("memory/tests/soul.md"),
        model_client=model_client,
        model_router=model_router,
    )
    service.soul_store = harness.soul_store
    service._profile = service._load_profile()
    return service, harness
