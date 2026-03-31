from __future__ import annotations

import json
from enum import StrEnum
from os import getenv

from pydantic import BaseModel, Field

from app.core.types import ProviderName
from app.tool.mcp_provider import MCPServerConfig


class ModelRole(StrEnum):
    DIALOGUE = "dialogue"
    EXECUTOR = "executor"
    DECISION = "decision"
    MEMORY = "memory"


class ModelRoute(BaseModel):
    provider: str = ""
    model: str = ""
    api_key: str = ""
    base_url: str = ""
    temperature: float = 0.3
    max_tokens: int = 800
    timeout_seconds: float = 30.0

    def is_configured(self) -> bool:
        provider = self.normalized_provider()
        if provider == ProviderName.CUSTOM:
            return bool(self.model and self.base_url)
        return bool(self.model)

    def normalized_provider(self) -> str:
        provider = str(self.provider or ProviderName.CUSTOM).strip().lower()
        return provider or ProviderName.CUSTOM


class ModelRoutingSettings(BaseModel):
    dialogue: ModelRoute = ModelRoute()
    executor: ModelRoute = ModelRoute()
    decision: ModelRoute = ModelRoute()
    memory: ModelRoute = ModelRoute()

    @classmethod
    def from_env(cls) -> ModelRoutingSettings:
        return cls(
            dialogue=ModelRoute(
                provider=getenv("AMADEUS_DIALOGUE_PROVIDER", ProviderName.CUSTOM),
                model=getenv("AMADEUS_DIALOGUE_MODEL", ""),
                api_key=getenv("AMADEUS_DIALOGUE_API_KEY", ""),
                base_url=getenv("AMADEUS_DIALOGUE_BASE_URL", ""),
                timeout_seconds=_env_float("AMADEUS_DIALOGUE_TIMEOUT_SECONDS", 30.0),
            ),
            executor=ModelRoute(
                provider=getenv("AMADEUS_EXECUTOR_PROVIDER", ProviderName.CUSTOM),
                model=getenv("AMADEUS_EXECUTOR_MODEL", ""),
                api_key=getenv("AMADEUS_EXECUTOR_API_KEY", ""),
                base_url=getenv("AMADEUS_EXECUTOR_BASE_URL", ""),
                timeout_seconds=_env_float("AMADEUS_EXECUTOR_TIMEOUT_SECONDS", 30.0),
            ),
            decision=ModelRoute(
                provider=getenv("AMADEUS_DECISION_PROVIDER", ProviderName.CUSTOM),
                model=getenv("AMADEUS_DECISION_MODEL", ""),
                api_key=getenv("AMADEUS_DECISION_API_KEY", ""),
                base_url=getenv("AMADEUS_DECISION_BASE_URL", ""),
                timeout_seconds=_env_float("AMADEUS_DECISION_TIMEOUT_SECONDS", 30.0),
            ),
            memory=ModelRoute(
                provider=getenv("AMADEUS_MEMORY_PROVIDER", ProviderName.CUSTOM),
                model=getenv("AMADEUS_MEMORY_MODEL", ""),
                api_key=getenv("AMADEUS_MEMORY_API_KEY", ""),
                base_url=getenv("AMADEUS_MEMORY_BASE_URL", ""),
                timeout_seconds=_env_float("AMADEUS_MEMORY_TIMEOUT_SECONDS", 30.0),
            ),
        )


class EmbeddingRoute(BaseModel):
    provider: str = ""
    model: str = ""
    api_key: str = ""
    base_url: str = ""
    dimensions: int | None = None
    timeout_seconds: float = 20.0

    def is_configured(self) -> bool:
        provider = self.normalized_provider()
        if provider == ProviderName.CUSTOM:
            return bool(self.model and self.base_url)
        if provider in {ProviderName.OPENAI, "alibaba"}:
            return bool(self.model)
        return bool(self.model and self.base_url)

    def normalized_provider(self) -> str:
        provider = str(self.provider or ProviderName.CUSTOM).strip().lower()
        return provider or ProviderName.CUSTOM


class MemoryEmbeddingSettings(BaseModel):
    semantic: EmbeddingRoute = EmbeddingRoute()

    @classmethod
    def from_env(cls) -> MemoryEmbeddingSettings:
        provider = (
            getenv(
                "AMADEUS_MEMORY_SEMANTIC_EMBEDDING_PROVIDER",
                getenv("AMADEUS_MEMORY_PROVIDER", ProviderName.CUSTOM),
            )
            .strip()
            .lower()
        ) or ProviderName.CUSTOM
        return cls(
            semantic=EmbeddingRoute(
                provider=provider,
                model=getenv("AMADEUS_MEMORY_SEMANTIC_EMBEDDING_MODEL", "").strip(),
                api_key=_embedding_api_key_from_env(provider),
                base_url=getenv(
                    "AMADEUS_MEMORY_SEMANTIC_EMBEDDING_BASE_URL",
                    getenv("AMADEUS_MEMORY_BASE_URL", ""),
                ).strip(),
                dimensions=_env_optional_int("AMADEUS_MEMORY_SEMANTIC_EMBEDDING_DIMENSIONS"),
                timeout_seconds=max(
                    1.0,
                    _env_float("AMADEUS_MEMORY_SEMANTIC_EMBEDDING_TIMEOUT_SECONDS", 20.0),
                ),
            )
        )


class MemoryRetrievalSettings(BaseModel):
    semantic_enabled: bool = True
    bm25_enabled: bool = True
    emotional_enabled: bool = True
    reranker_enabled: bool = True
    candidate_pool_size: int = 12

    @classmethod
    def from_env(cls) -> MemoryRetrievalSettings:
        return cls(
            semantic_enabled=_env_bool("AMADEUS_MEMORY_RETRIEVAL_SEMANTIC_ENABLED", True),
            bm25_enabled=_env_bool("AMADEUS_MEMORY_RETRIEVAL_BM25_ENABLED", True),
            emotional_enabled=_env_bool("AMADEUS_MEMORY_RETRIEVAL_EMOTIONAL_ENABLED", True),
            reranker_enabled=_env_bool("AMADEUS_MEMORY_RETRIEVAL_RERANKER_ENABLED", True),
            candidate_pool_size=max(
                1,
                _env_int("AMADEUS_MEMORY_RETRIEVAL_CANDIDATE_POOL_SIZE", 12),
            ),
        )


class MemoryStorageSettings(BaseModel):
    active_retention_days: int = 999

    @classmethod
    def from_env(cls) -> MemoryStorageSettings:
        return cls(
            active_retention_days=max(
                1,
                _env_int("AMADEUS_MEMORY_ACTIVE_RETENTION_DAYS", 999),
            )
        )


class ExecutionSettings(BaseModel):
    max_inner_loop_turns: int = 7
    loop_pre_replan_buffer_seconds: int = 30

    @classmethod
    def from_env(cls) -> ExecutionSettings:
        raw_turn_value = getenv("AMADEUS_EXECUTION_MAX_INNER_LOOP_TURNS", "7").strip()
        raw_buffer_value = getenv(
            "AMADEUS_EXECUTION_LOOP_PRE_REPLAN_BUFFER_SECONDS",
            "30",
        ).strip()
        try:
            parsed_turns = int(raw_turn_value)
        except ValueError:
            parsed_turns = 7
        try:
            parsed_buffer = int(raw_buffer_value)
        except ValueError:
            parsed_buffer = 30
        return cls(
            max_inner_loop_turns=max(1, parsed_turns),
            loop_pre_replan_buffer_seconds=max(0, parsed_buffer),
        )


class MCPSettings(BaseModel):
    servers: list[MCPServerConfig] = Field(default_factory=list)

    @classmethod
    def from_env(cls) -> MCPSettings:
        raw_payload = getenv("AMADEUS_MCP_SERVERS_JSON", "").strip()
        if not raw_payload:
            return cls()
        try:
            payload = json.loads(raw_payload)
        except ValueError:
            return cls()
        if isinstance(payload, dict):
            payload = [payload]
        if not isinstance(payload, list):
            return cls()
        servers: list[MCPServerConfig] = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            try:
                servers.append(MCPServerConfig.model_validate(item))
            except Exception:
                continue
        return cls(servers=servers)


def _env_bool(name: str, default: bool) -> bool:
    raw = getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    raw = getenv(name)
    if raw is None:
        return default
    try:
        return int(raw.strip())
    except ValueError:
        return default


def _env_optional_int(name: str) -> int | None:
    raw = getenv(name)
    if raw is None or not raw.strip():
        return None
    try:
        return int(raw.strip())
    except ValueError:
        return None


def _env_float(name: str, default: float) -> float:
    raw = getenv(name)
    if raw is None:
        return default
    try:
        return float(raw.strip())
    except ValueError:
        return default


def _embedding_api_key_from_env(provider: str) -> str:
    explicit = getenv(
        "AMADEUS_MEMORY_SEMANTIC_EMBEDDING_API_KEY",
        getenv("AMADEUS_MEMORY_API_KEY", ""),
    ).strip()
    if explicit:
        return explicit
    if provider == "alibaba":
        return getenv("ALIBABA_API_KEY", getenv("DASHSCOPE_API_KEY", "")).strip()
    if provider == ProviderName.OPENAI:
        return getenv("OPENAI_API_KEY", "").strip()
    return ""
