from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.core.events import EventSource, EventType, RuntimeEvent
from app.core.outcomes import ActionOutcome, OutcomeStatus
from app.core.state import PlanStep, RuntimeState
from app.core.types import ExecutionMode, ExecutionZone
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
from app.memory.service import MemoryService
from app.runtime.planning import PlanningService
from tests.test_support import InMemoryJsonlStore, MemoryHarness, build_in_memory_memory_service


class PromptCapturingModelClient(ModelClient):
    def __init__(self) -> None:
        self.prompts: list[str] = []
        self.system_prompts: list[str] = []

    async def generate_text(self, request: ModelRequest) -> TextResponse:
        return TextResponse(text=request.prompt)

    async def generate_structured(self, request: ModelRequest, schema_type):
        self.prompts.append(request.prompt)
        self.system_prompts.append(request.system_prompt)
        if schema_type.__name__ == "DayPlanDraft":
            payload = {
                "items": [
                    {"time": "08:00-09:30", "label": "接住昨天留下的主线"},
                    {"time": "09:30-12:00", "label": "先把早上的状态稳定下来"},
                ]
            }
        elif schema_type.__name__ == "MinuteActionPlanDraft":
            payload = {
                "items": [
                    {
                        "action_description": "检查昨天留下的上下文",
                        "duration_minutes": 5,
                    }
                ]
            }
        else:
            payload = {}
        return StructuredResponse(
            structured=schema_type.model_validate(payload)
        )


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


def configured_planning_router() -> ModelRouter:
    return ModelRouter(
        settings=ModelRoutingSettings(
            decision=ModelRoute(
                provider="custom",
                model="decision-x",
                base_url="https://mock/decision",
            )
        )
    )


def test_raw_log_store_uses_date_named_folders(tmp_path: Path) -> None:
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

    reloaded = MemoryService(raw_log_path=tmp_path / "raw_log")
    assert [entry.payload["label"] for entry in reloaded.raw_entries] == ["first", "second"]


@pytest.mark.anyio
async def test_planning_model_trace_is_written_to_raw_log() -> None:
    memory_service, _ = build_in_memory_memory_service()
    model_client = PromptCapturingModelClient()
    planning = PlanningService(
        model_client=model_client,
        model_router=configured_planning_router(),
        memory_service=memory_service,
    )

    plan = await planning.plan_next_window(
        state=RuntimeState(),
        trigger_event=RuntimeEvent(
            event_type=EventType.DAY_START,
            source=EventSource.SYSTEM,
        ),
        now=datetime(2026, 3, 24, 0, 5, tzinfo=UTC),
    )

    planning_entries = [entry for entry in memory_service.raw_entries if entry.kind == "planning"]
    assert planning_entries
    latest = planning_entries[-1]
    assert latest.payload["strategy"] == "model"
    assert latest.payload["prompt"]
    assert latest.payload["system_prompt"]
    assert latest.payload["structured_output"][0]["label"] == "接住昨天留下的主线"
    assert latest.payload["plan_state"]["day_summary"] == plan.day_summary


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


def test_core_memory_stores_soul_markdown_and_day_scoped_execution_records() -> None:
    memory_service, _ = build_in_memory_memory_service()
    memory_service.update_persona_context(
        soul_md="# 灵魂档案：Kurisu\n\n## 核心设定\nA careful researcher.",
    )
    memory_service.update_plan_context(
        plan_summary="Keep the current research thread coherent.",
        plan_date="2026-03-26",
    )

    step = PlanStep(
        title="Check the carry-over context",
        detail="Review yesterday's notes before continuing.",
        completed_at="2026-03-26T09:05:00+00:00",
    )
    outcome = ActionOutcome(
        action_id=step.step_id,
        status=OutcomeStatus.SUCCESS,
        mode=ExecutionMode.NARRATIVE,
        source=ExecutionZone.WEAK_REAL,
        content="Reviewed the notes and found the next clean handoff.",
    )
    memory_service.record_outcome(
        step,
        outcome,
        memory_content="Checked the carry-over context and found the next clean handoff.",
    )

    assert memory_service.core_memory.soul_md.startswith("# 灵魂档案：Kurisu")
    assert memory_service.core_memory.core_date == "2026-03-26"
    assert len(memory_service.core_memory.today_execution_records) == 1
    assert (
        memory_service.core_memory.today_execution_records[0].content
        == "Checked the carry-over context and found the next clean handoff."
    )
    assert memory_service.core_memory.recent_events == [
        "Checked the carry-over context and found the next clean handoff."
    ]

    memory_service.update_plan_context(
        plan_summary="Start the next day from a clean slate.",
        plan_date="2026-03-27",
    )

    assert memory_service.core_memory.core_date == "2026-03-27"
    assert memory_service.core_memory.today_plan_summary == "Start the next day from a clean slate."
    assert memory_service.core_memory.today_execution_records == []
    assert memory_service.core_memory.recent_events == []


@pytest.mark.anyio
async def test_planning_prompt_uses_core_memory_shape() -> None:
    memory_service, _ = build_in_memory_memory_service()
    memory_service.update_persona_context(
        soul_md="# 灵魂档案：Kurisu\n\n## 核心设定\nA careful researcher.",
    )
    memory_service.update_plan_context(
        plan_summary="Keep the current thread coherent.",
        plan_date="2026-03-24",
    )
    memory_service.record_outcome(
        PlanStep(
            title="Wrap the last block",
            detail="Leave a clean note for tomorrow.",
            completed_at="2026-03-24T00:02:00+00:00",
        ),
        ActionOutcome(
            action_id="step_wrap",
            status=OutcomeStatus.SUCCESS,
            mode=ExecutionMode.NARRATIVE,
            source=ExecutionZone.WEAK_REAL,
            content="Wrapped the last block and left a clean note.",
        ),
        memory_content="Wrapped the last block and left a clean note.",
    )
    model_client = PromptCapturingModelClient()
    planning = PlanningService(
        model_client=model_client,
        model_router=configured_planning_router(),
        memory_service=memory_service,
    )

    await planning.plan_next_window(
        state=RuntimeState(),
        trigger_event=RuntimeEvent(
            event_type=EventType.DAY_START,
            source=EventSource.SYSTEM,
        ),
        now=datetime(2026, 3, 24, 9, 0, tzinfo=UTC),
    )

    prompt = model_client.prompts[0]

    assert "# 灵魂档案：Kurisu" in model_client.system_prompts[0]
    assert "Wrapped the last block and left a clean note." in prompt
    assert "昨天发生了这些事情：" in prompt
