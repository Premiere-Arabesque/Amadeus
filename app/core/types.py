from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4

type JsonValue = Any


def utc_now() -> datetime:
    return datetime.now(UTC)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


class ProviderName(StrEnum):
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    CUSTOM = "custom"


class ExecutionMode(StrEnum):
    TOOL = "tool"
    HYBRID = "hybrid"
    NARRATIVE = "narrative"


class ExecutionZone(StrEnum):
    REAL = "real"
    NON_REAL = "non_real"

    @classmethod
    def _missing_(cls, value: object):
        if not isinstance(value, str):
            return None
        normalized = value.strip().lower().replace("-", "_").replace(" ", "_")
        legacy_map = {
            "real": cls.REAL,
            "real_zone": cls.REAL,
            "weak_real": cls.NON_REAL,
            "weak_real_zone": cls.NON_REAL,
            "ambiguity": cls.NON_REAL,
            "ambiguity_zone": cls.NON_REAL,
            "non_real": cls.NON_REAL,
            "non_real_zone": cls.NON_REAL,
        }
        return legacy_map.get(normalized)
