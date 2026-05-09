from datetime import datetime
from pathlib import Path

import pytest

from app.infra.model_client import (
    ModelClient,
    ModelRequest,
    ModelRouter,
    StructuredResponse,
    TextResponse,
)
from app.infra.settings import MemoryRetrievalSettings, ModelRoute, ModelRoutingSettings
from app.memory.models import ActiveMemoryEntry, RawLogEntry
from app.memory.retrieval import MemoryRetrievalPipeline
from app.runtime.roleplay_context import RoleplayAgentContext
from tests.test_support import InMemoryJsonlStore, MemoryHarness, build_in_memory_memory_service


class RerankCapturingModelClient(ModelClient):
    def __init__(self, ranked_entry_ids: list[str]) -> None:
        self.prompts: list[str] = []
        self.ranked_entry_ids = ranked_entry_ids

    async def generate_text(self, request: ModelRequest) -> TextResponse:
        return TextResponse(text=request.prompt)

    async def generate_structured(self, request: ModelRequest, schema_type):
        self.prompts.append(request.prompt)
        fields = getattr(schema_type, "model_fields", {})
        if "ranked_entry_ids" in fields:
            return StructuredResponse(
                structured=schema_type.model_validate(
                    {"ranked_entry_ids": self.ranked_entry_ids}
                )
            )
        return StructuredResponse(structured=schema_type.model_validate({}))


def test_raw_log_store_uses_date_named_folders(tmp_path: Path) -> None:
    from app.memory.service import MemoryService

    service = MemoryService(raw_log_path=tmp_path / "raw_log")

    service._append_raw(
        RawLogEntry(
            kind="event",
            source="system",
            created_at="2026-03-26T10:00:00+00:00",
            payload={"label": "first"},
        )
    )
    service._append_raw(
        RawLogEntry(
            kind="event",
            source="system",
            created_at="2026-03-27T09:00:00+00:00",
            payload={"label": "second"},
        )
    )

    assert (tmp_path / "raw_log" / "2026-03-26" / "entries.jsonl").exists()
    assert (tmp_path / "raw_log" / "2026-03-27" / "entries.jsonl").exists()


def test_core_prompt_context_defaults_to_soul_md_only() -> None:
    from app.memory.service import MemoryService

    service = MemoryService()
    service.core_memory.soul_md = "I am Kurisu."

    prompt = service.core_prompt_context(state=None, execution_limit=4)

    assert "soul.md:" in prompt
    assert "I am Kurisu." in prompt
    assert "长期稳定事实" not in prompt
    assert "长期关系结论" not in prompt
    assert "长期重要结论" not in prompt


@pytest.mark.anyio
async def test_retrieval_pipeline_degrades_to_bm25_when_optional_stages_are_unavailable() -> None:
    entries = [
        ActiveMemoryEntry(content="hello amadeus from the memory log", source="interaction"),
        ActiveMemoryEntry(content="something unrelated", source="interaction"),
    ]
    pipeline = MemoryRetrievalPipeline(settings=MemoryRetrievalSettings())

    ranked = await pipeline.rank("hello amadeus", entries, top_k=1)

    assert ranked
    assert ranked[0].content == "hello amadeus from the memory log"


@pytest.mark.anyio
async def test_retrieval_pipeline_can_use_semantic_stage_without_lexical_overlap() -> None:
    entries = [
        ActiveMemoryEntry(
            content="ordinary afternoon routine",
            source="interaction",
            semantic_embedding=[1.0, 0.0],
        ),
        ActiveMemoryEntry(
            content="another unrelated note",
            source="interaction",
            semantic_embedding=[0.0, 1.0],
        ),
    ]
    pipeline = MemoryRetrievalPipeline(
        settings=MemoryRetrievalSettings(
            semantic_enabled=True,
            bm25_enabled=False,
            emotional_enabled=False,
            reranker_enabled=False,
        ),
        semantic_query_embedder=lambda _: [1.0, 0.0],
    )

    ranked = await pipeline.rank("banana thread", entries, top_k=1)

    assert ranked
    assert ranked[0].content == "ordinary afternoon routine"


@pytest.mark.anyio
async def test_memory_service_uses_model_reranker_on_deduped_candidates() -> None:
    model_client = RerankCapturingModelClient(ranked_entry_ids=["mem_semantic", "mem_both"])
    memory_service, _ = build_in_memory_memory_service(
        model_client=model_client,
        model_router=ModelRouter(
            settings=ModelRoutingSettings(
                memory=ModelRoute(
                    provider="custom",
                    model="memory-x",
                    base_url="https://mock/memory",
                )
            )
        ),
        harness=MemoryHarness(
            active_store=InMemoryJsonlStore(
                [
                    ActiveMemoryEntry(
                        entry_id="mem_both",
                        content="deadline tomorrow for the current thread",
                        source="interaction",
                        semantic_embedding=[1.0, 0.0],
                    ).model_dump(mode="json"),
                    ActiveMemoryEntry(
                        entry_id="mem_semantic",
                        content="project continuity handoff",
                        source="interaction",
                        semantic_embedding=[1.0, 0.0],
                    ).model_dump(mode="json"),
                ]
            )
        ),
    )
    memory_service.retrieval_pipeline.semantic_query_embedder = lambda _: [1.0, 0.0]

    ranked = await memory_service.replan_memory_context(
        query_text="deadline current thread",
        top_k=2,
    )

    assert ranked == [
        "project continuity handoff",
        "deadline tomorrow for the current thread",
    ]
    prompt = model_client.prompts[0]
    assert prompt.count('"entry_id": "mem_both"') == 1
    assert '"hit_stages": [' in prompt
    assert '"bm25"' in prompt
    assert '"semantic"' in prompt


@pytest.mark.anyio
async def test_retrieve_memories_dedupes_duplicate_contents_before_returning() -> None:
    memory_service, _ = build_in_memory_memory_service(
        harness=MemoryHarness(
            active_store=InMemoryJsonlStore(
                [
                    ActiveMemoryEntry(
                        entry_id="mem_a",
                        content="deadline tomorrow for the current thread",
                        source="interaction",
                        semantic_embedding=[1.0, 0.0],
                    ).model_dump(mode="json"),
                    ActiveMemoryEntry(
                        entry_id="mem_b",
                        content="deadline tomorrow for the current thread",
                        source="interaction",
                        semantic_embedding=[1.0, 0.0],
                    ).model_dump(mode="json"),
                    ActiveMemoryEntry(
                        entry_id="mem_c",
                        content="current thread handoff details for tomorrow",
                        source="interaction",
                        semantic_embedding=[1.0, 0.0],
                    ).model_dump(mode="json"),
                ]
            )
        ),
        semantic_query_embedder=lambda _: [1.0, 0.0],
    )

    memories = await memory_service.retrieve_memories(
        query_text="deadline current thread",
        top_k=3,
        reranker_enabled=False,
    )

    assert memories == [
        "deadline tomorrow for the current thread",
        "current thread handoff details for tomorrow",
    ]


@pytest.mark.anyio
async def test_retrieve_and_inject_memories_appends_roleplay_context_entry() -> None:
    memory_service, _ = build_in_memory_memory_service(
        harness=MemoryHarness(
            active_store=InMemoryJsonlStore(
                [
                    ActiveMemoryEntry(
                        entry_id="mem_a",
                        content="deadline tomorrow for the current thread",
                        source="interaction",
                        semantic_embedding=[1.0, 0.0],
                    ).model_dump(mode="json"),
                    ActiveMemoryEntry(
                        entry_id="mem_b",
                        content="current thread handoff details for tomorrow",
                        source="interaction",
                        semantic_embedding=[1.0, 0.0],
                    ).model_dump(mode="json"),
                ]
            )
        ),
        semantic_query_embedder=lambda _: [1.0, 0.0],
    )
    context = RoleplayAgentContext()

    entry = await memory_service.retrieve_and_inject_memories(
        query_text="deadline current thread",
        context=context,
        roleplay_name="牧濑红莉栖",
        source="execution_scene",
        reranker_enabled=False,
        metadata={"turn": 1},
    )

    assert entry is not None
    assert context.entries[-1] == entry
    assert entry.kind == "retrieved_memory"
    assert "你想起了一些事情：" in entry.content
    assert "- deadline tomorrow for the current thread" in entry.content
    assert "- current thread handoff details for tomorrow" in entry.content
    assert entry.metadata["source"] == "execution_scene"
    assert entry.metadata["query_text"] == "deadline current thread"
    assert entry.metadata["turn"] == 1


@pytest.mark.anyio
async def test_retrieve_and_inject_interaction_memories_prefers_same_partner() -> None:
    memory_service, _ = build_in_memory_memory_service(
        harness=MemoryHarness(
            active_store=InMemoryJsonlStore(
                [
                    ActiveMemoryEntry(
                        entry_id="mem_mayuri",
                        content="Mayuri mentioned the urgent banana experiment issue yesterday.",
                        source="interaction",
                        interaction_partner="Mayuri",
                        semantic_embedding=[1.0, 0.0],
                    ).model_dump(mode="json"),
                    ActiveMemoryEntry(
                        entry_id="mem_luka",
                        content="Luka asked for a paper summary about the same topic.",
                        source="interaction",
                        interaction_partner="Luka",
                        semantic_embedding=[1.0, 0.0],
                    ).model_dump(mode="json"),
                ]
            )
        ),
        semantic_query_embedder=lambda _: [1.0, 0.0],
    )
    context = RoleplayAgentContext()

    entry = await memory_service.retrieve_and_inject_interaction_memories(
        query_text="Mayuri said the banana issue is urgent",
        context=context,
        roleplay_name="牧濑红莉栖",
        interaction_partner="Mayuri",
        reranker_enabled=False,
    )

    assert entry is not None
    assert "Mayuri mentioned the urgent banana experiment issue yesterday." in entry.content
    assert "Luka asked for a paper summary" not in entry.content
    assert entry.metadata["interaction_partner"] == "Mayuri"
    assert entry.metadata["retrieval_scope"] == "partner_only"


@pytest.mark.anyio
async def test_retrieve_and_inject_interaction_memories_can_fallback_globally() -> None:
    memory_service, _ = build_in_memory_memory_service(
        harness=MemoryHarness(
            active_store=InMemoryJsonlStore(
                [
                    ActiveMemoryEntry(
                        entry_id="mem_global",
                        content="Someone recently mentioned a very urgent deadline around the current thread.",
                        source="interaction",
                        interaction_partner="Luka",
                        semantic_embedding=[1.0, 0.0],
                    ).model_dump(mode="json"),
                ]
            )
        ),
        semantic_query_embedder=lambda _: [1.0, 0.0],
    )
    context = RoleplayAgentContext()

    entry = await memory_service.retrieve_and_inject_interaction_memories(
        query_text="The user says this is urgent",
        context=context,
        roleplay_name="牧濑红莉栖",
        interaction_partner="Mayuri",
        reranker_enabled=False,
    )

    assert entry is not None
    assert "Someone recently mentioned a very urgent deadline around the current thread." in entry.content
    assert entry.metadata["interaction_partner"] == "Mayuri"
    assert entry.metadata["retrieval_scope"] == "global_fallback"


def test_active_memory_uses_time_window_and_rolls_stale_entries_to_archive() -> None:
    memory_service, _ = build_in_memory_memory_service(
        harness=MemoryHarness(
            active_store=InMemoryJsonlStore(
                [
                    ActiveMemoryEntry(
                        created_at="2020-01-01T09:00:00+00:00",
                        content="stale hello amadeus note",
                        source="interaction",
                    ).model_dump(mode="json"),
                    ActiveMemoryEntry(
                        created_at="2099-03-26T09:00:00+00:00",
                        content="fresh hello amadeus note",
                        source="interaction",
                    ).model_dump(mode="json"),
                ]
            )
        ),
        active_retention_days=1,
    )

    active_entries = memory_service.recent_active_entries(limit=10)
    archive_entries = memory_service.recent_archive_entries(limit=10)

    assert [entry.content for entry in active_entries] == ["fresh hello amadeus note"]
    assert [entry.content for entry in archive_entries] == ["stale hello amadeus note"]


def test_roleplay_context_persists_in_separate_store() -> None:
    memory_service, harness = build_in_memory_memory_service()
    persisted = RoleplayAgentContext(
        soul_md="Kurisu soul",
        plan_context="09:00-12:00 Study",
    )
    persisted.add_execution_record(
        roleplay="去图书馆",
        scene="你背着包走出门。",
        result="你已经在去图书馆的路上。",
    )

    memory_service.save_roleplay_agent_context(persisted)

    restarted_memory_service, _ = build_in_memory_memory_service(harness=harness)
    reloaded = restarted_memory_service.get_persisted_roleplay_agent_context()

    assert reloaded.soul_md == "Kurisu soul"
    assert reloaded.plan_context == "09:00-12:00 Study"
    assert len(reloaded.entries) == 1
    assert reloaded.entries[0].kind == "execution_record"
    assert "去图书馆" in reloaded.entries[0].content


def test_roleplay_context_rotation_moves_entries_to_previous_day_bucket() -> None:
    memory_service, _ = build_in_memory_memory_service()
    context = RoleplayAgentContext(
        context_date="2026-04-03",
        soul_md="Kurisu soul",
        plan_context="19:00-21:00 放松",
    )
    context.add_execution_record(
        roleplay="刷小红书",
        scene="你躺在床上滑动推荐流。",
        result="你看到了几条穿搭和咖啡店帖子。",
    )
    memory_service.save_roleplay_agent_context(context)

    rotated = memory_service.rotate_roleplay_agent_context_for_day(target_date="2026-04-04")

    assert rotated.context_date == "2026-04-04"
    assert rotated.entries == []
    assert rotated.plan_context == ""
    assert rotated.previous_context_date == "2026-04-03"
    assert len(rotated.previous_entries) == 1
    assert "刷小红书" in rotated.previous_entries[0].content


def test_day_start_memory_context_prefers_previous_roleplay_context_entries() -> None:
    memory_service, _ = build_in_memory_memory_service()
    context = RoleplayAgentContext(
        context_date="2026-04-03",
        soul_md="Kurisu soul",
    )
    context.add_execution_record(
        roleplay="刷小红书",
        scene="你躺在床上滑动推荐流。",
        result="你看到了几条穿搭和咖啡店帖子。",
    )
    memory_service.save_roleplay_agent_context(context)
    memory_service.rotate_roleplay_agent_context_for_day(target_date="2026-04-04")

    memories = memory_service.day_start_memory_context(
        now=datetime.fromisoformat("2026-04-04T00:00:00+00:00"),
        limit=3,
    )

    assert memories
    assert "刷小红书" in memories[-1]
