from datetime import UTC, datetime

import httpx

from app.core.outcomes import ActionOutcome, OutcomeStatus
from app.core.state import PlanStep
from app.core.types import ExecutionMode, ExecutionZone
from app.infra.embeddings import build_semantic_embedder
from app.infra.settings import EmbeddingRoute, MemoryEmbeddingSettings
from tests.test_support import build_in_memory_memory_service


def test_memory_embedding_settings_inherit_memory_provider_and_key(monkeypatch) -> None:
    monkeypatch.delenv("AMADEUS_MEMORY_SEMANTIC_EMBEDDING_API_KEY", raising=False)
    monkeypatch.delenv("ALIBABA_API_KEY", raising=False)
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    monkeypatch.setenv("AMADEUS_MEMORY_PROVIDER", "alibaba")
    monkeypatch.setenv("AMADEUS_MEMORY_API_KEY", "memory-key")
    monkeypatch.setenv("AMADEUS_MEMORY_SEMANTIC_EMBEDDING_MODEL", "text-embedding-v4")

    settings = MemoryEmbeddingSettings.from_env()

    assert settings.semantic.provider == "alibaba"
    assert settings.semantic.api_key == "memory-key"
    assert settings.semantic.model == "text-embedding-v4"


def test_alibaba_semantic_embedder_uses_dashscope_compatible_endpoint() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://dashscope-intl.aliyuncs.com/compatible-mode/v1/embeddings"
        assert request.headers["Authorization"] == "Bearer dash-key"
        payload = request.read().decode("utf-8")
        assert '"model":"text-embedding-v4"' in payload
        assert '"input":"test semantic memory"' in payload
        return httpx.Response(
            status_code=200,
            json={
                "data": [
                    {
                        "embedding": [0.1, 0.2, 0.3],
                    }
                ]
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    try:
        embedder = build_semantic_embedder(
            MemoryEmbeddingSettings(
                semantic=EmbeddingRoute(
                    provider="alibaba",
                    model="text-embedding-v4",
                    api_key="dash-key",
                )
            ),
            http_client=client,
        )
        assert embedder is not None
        assert embedder("test semantic memory") == [0.1, 0.2, 0.3]
    finally:
        client.close()


def test_memory_service_records_semantic_embedding_when_embedder_is_configured() -> None:
    memory_service, _ = build_in_memory_memory_service(
        semantic_entry_embedder=lambda _: [1.0, 0.0],
        semantic_query_embedder=lambda _: [1.0, 0.0],
    )

    memory_service.record_outcome(
        PlanStep(
            title="Capture semantic continuity",
            detail="Persist the important handoff.",
            completed_at=datetime(2026, 3, 27, 9, 0, tzinfo=UTC).isoformat(),
        ),
        ActionOutcome(
            action_id="step_semantic",
            status=OutcomeStatus.SUCCESS,
            mode=ExecutionMode.NARRATIVE,
            source=ExecutionZone.WEAK_REAL,
            content="Saved the continuity note.",
        ),
        memory_content="Saved the continuity note.",
    )

    assert memory_service.active_entries[-1].semantic_embedding == [1.0, 0.0]
