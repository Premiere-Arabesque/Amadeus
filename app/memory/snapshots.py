from __future__ import annotations

from app.core.state import RuntimeState
from app.memory.models import RuntimeSnapshot


def make_snapshot(state: RuntimeState) -> RuntimeSnapshot:
    return RuntimeSnapshot(state=state.model_dump(mode="json"))
