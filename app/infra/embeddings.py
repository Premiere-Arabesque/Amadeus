from __future__ import annotations

from typing import Any

import httpx

from app.core.types import ProviderName
from app.infra.settings import EmbeddingRoute, MemoryEmbeddingSettings
from app.memory.retrieval import EmbeddingGenerator

DEFAULT_OPENAI_BASE_URL = "https://api.openai.com/v1"
DEFAULT_ALIBABA_BASE_URL = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"


class OpenAICompatibleEmbeddingClient:
    def __init__(self, http_client: httpx.Client | None = None) -> None:
        self.http_client = http_client or httpx.Client()

    def build_embedder(self, route: EmbeddingRoute) -> EmbeddingGenerator | None:
        if not route.is_configured():
            return None

        def embed(text: str) -> list[float] | None:
            content = text.strip()
            if not content:
                return None
            response = self.http_client.post(
                f"{_resolve_base_url(route)}/embeddings",
                headers=_build_headers(route),
                json=_build_payload(route, content),
                timeout=route.timeout_seconds,
            )
            response.raise_for_status()
            return _parse_embedding(response.json())

        return embed


def build_semantic_embedder(
    settings: MemoryEmbeddingSettings,
    *,
    http_client: httpx.Client | None = None,
) -> EmbeddingGenerator | None:
    return OpenAICompatibleEmbeddingClient(http_client=http_client).build_embedder(
        settings.semantic
    )


def _resolve_base_url(route: EmbeddingRoute) -> str:
    if route.base_url:
        return route.base_url.rstrip("/")
    provider = route.normalized_provider()
    if provider == "alibaba":
        return DEFAULT_ALIBABA_BASE_URL
    if provider == ProviderName.OPENAI:
        return DEFAULT_OPENAI_BASE_URL
    raise RuntimeError(
        "Semantic embedding provider requires an explicit base_url when no built-in default "
        f"exists: {provider!r}"
    )


def _build_headers(route: EmbeddingRoute) -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if route.api_key:
        headers["Authorization"] = f"Bearer {route.api_key}"
    return headers


def _build_payload(route: EmbeddingRoute, text: str) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": route.model,
        "input": text,
    }
    if route.dimensions is not None:
        payload["dimensions"] = route.dimensions
    return payload


def _parse_embedding(payload: Any) -> list[float]:
    if not isinstance(payload, dict):
        raise RuntimeError("Embedding response payload is not a JSON object.")
    data = payload.get("data")
    if not isinstance(data, list) or not data:
        raise RuntimeError("Embedding response payload does not contain any data rows.")
    first_item = data[0]
    if not isinstance(first_item, dict):
        raise RuntimeError("Embedding response data row is malformed.")
    embedding = first_item.get("embedding")
    if not isinstance(embedding, list):
        raise RuntimeError("Embedding response row does not contain an embedding array.")
    return [float(value) for value in embedding]
