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


class ExecutionMode(StrEnum):
    TOOL = "tool"
    HYBRID = "hybrid"
    NARRATIVE = "narrative"
