from datetime import UTC, datetime

import pytest

from app.core.events import EventSource, EventType, RuntimeEvent
from app.core.outcomes import ReplanKind
from app.core.state import PlanState, RuntimeState
from app.core.types import ExecutionMode
from app.infra.model_client import (
    ModelClient,
    ModelRequest,
    ModelRouter,
    StructuredResponse,
    TextResponse,
)
from app.infra.settings import ModelRoute, ModelRoutingSettings
from app.memory.models import ActiveMemoryEntry
from app.runtime.planning import PlanningService
from tests.test_support import (
    InMemoryJsonlStore,
    MemoryHarness,
    build_in_memory_memory_service,
)


class PromptCapturingModelClient(ModelClient):
    def __init__(self) -> None:
        self.prompts: list[str] = []
        self.day_plan_prompts: list[str] = []
        self.minute_action_prompts: list[str] = []

    async def generate_text(self, request: ModelRequest) -> TextResponse:
        self.prompts.append(request.prompt)
        return TextResponse(text=request.prompt)

    async def generate_structured(self, request: ModelRequest, schema_type):
        self.prompts.append(request.prompt)
        if schema_type.__name__ == "DayPlanDraft":
            self.day_plan_prompts.append(request.prompt)
            payload = [
                {"time": "08:00-09:30", "label": "接住昨天实验结束前留下的线索"},
                {"time": "09:30-12:00", "label": "先把早上的状态稳定下来"},
                {"time": "13:00-15:00", "label": "推进白天最值得继续的主线"},
            ]
        elif schema_type.__name__ == "MinuteActionPlanDraft":
            self.minute_action_prompts.append(request.prompt)
            payload = [
                {
                    "action_description": "先检查昨天最后一次实验结果",
                    "duration_minutes": 5,
                },
                {
                    "action_description": "确认下一步最值得推进的动作",
                    "duration_minutes": 10,
                },
            ]
        else:
            payload = []
        return StructuredResponse(structured=schema_type.model_validate(payload))


@pytest.mark.anyio
async def test_boot_planning_creates_day_hour_and_minute_layers() -> None:
    planning = PlanningService()

    plan = await planning.plan_next_window(
        RuntimeState(),
        RuntimeEvent(
            event_type=EventType.SYSTEM_BOOT,
            source=EventSource.SYSTEM,
        ),
        now=datetime(2026, 3, 23, 14, 0, tzinfo=UTC),
    )

    assert plan.day_plan_items
    assert plan.active_day_item_id is not None
    assert plan.hour_plan_items
    assert plan.active_hour_item_id is not None
    assert plan.hour_plan_items[0].status == "active"
    assert plan.minute_steps


@pytest.mark.anyio
async def test_planning_routes_explicit_search_intent_to_search_web() -> None:
    planning = PlanningService()

    plan = await planning.plan_next_window(
        RuntimeState(),
        RuntimeEvent(
            event_type=EventType.MESSAGE_RECEIVED,
            source=EventSource.USER,
            payload={"text": "search: quantum bananas"},
        ),
        now=datetime(2026, 3, 23, 14, 0, tzinfo=UTC),
    )

    first_step = plan.minute_steps[0]
    assert first_step.execution_mode == ExecutionMode.HYBRID
    assert first_step.capability == "search_web"
    assert first_step.arguments["query"] == "quantum bananas"


@pytest.mark.anyio
async def test_planning_keeps_url_messages_on_read_url_even_with_search_language() -> None:
    planning = PlanningService()

    plan = await planning.plan_next_window(
        RuntimeState(),
        RuntimeEvent(
            event_type=EventType.MESSAGE_RECEIVED,
            source=EventSource.USER,
            payload={"text": "search: https://example.com/paper"},
        ),
        now=datetime(2026, 3, 23, 14, 0, tzinfo=UTC),
    )

    first_step = plan.minute_steps[0]
    assert first_step.execution_mode == ExecutionMode.HYBRID
    assert first_step.capability == "read_url"
    assert first_step.arguments["url"] == "https://example.com/paper"


@pytest.mark.anyio
async def test_day_start_planning_uses_previous_day_memories_only() -> None:
    previous_day_entry = ActiveMemoryEntry(
        created_at="2026-03-23T22:30:00+00:00",
        content="Wrapped the experiment at loss 0.8 before sleeping.",
        source="Weak Real Zone",
    )
    same_day_entry = ActiveMemoryEntry(
        created_at="2026-03-24T00:10:00+00:00",
        content="This entry should not be used for day-start retrieval.",
        source="Weak Real Zone",
    )
    memory_service, _ = build_in_memory_memory_service(
        harness=MemoryHarness(
            active_store=InMemoryJsonlStore(
                [
                    previous_day_entry.model_dump(mode="json"),
                    same_day_entry.model_dump(mode="json"),
                ]
            )
        )
    )
    model_client = PromptCapturingModelClient()
    planning = PlanningService(
        model_client=model_client,
        model_router=ModelRouter(
            settings=ModelRoutingSettings(
                decision=ModelRoute(
                    provider="custom",
                    model="decision-x",
                    base_url="https://mock/decision",
                )
            )
        ),
        memory_service=memory_service,
    )

    await planning.plan_next_window(
        RuntimeState(
            plan=PlanState(
                plan_date="2026-03-23",
                day_summary="Yesterday's plan.",
            )
        ),
        RuntimeEvent(
            event_type=EventType.DAY_START,
            source=EventSource.SYSTEM,
        ),
        now=datetime(2026, 3, 24, 0, 5, tzinfo=UTC),
    )

    day_prompt = model_client.day_plan_prompts[-1]

    assert "Wrapped the experiment at loss 0.8 before sleeping." in day_prompt
    assert "This entry should not be used for day-start retrieval." not in day_prompt
    assert "昨天发生了这些事情：" in day_prompt


@pytest.mark.anyio
async def test_replan_planning_expands_current_block_into_minute_actions() -> None:
    model_client = PromptCapturingModelClient()
    planning = PlanningService(
        model_client=model_client,
        model_router=ModelRouter(
            settings=ModelRoutingSettings(
                decision=ModelRoute(
                    provider="custom",
                    model="decision-x",
                    base_url="https://mock/decision",
                )
            )
        ),
        memory_service=None,
    )

    plan = await planning.replan_after_completion(
        RuntimeState(
            plan=PlanState(
                plan_date="2026-03-23",
                day_summary="Keep the current thread coherent.",
                current_hour_summary="Continue the current lab thread.",
            )
        ),
        now=datetime(2026, 3, 23, 14, 0, tzinfo=UTC),
        kind=ReplanKind.MICRO_REPLAN,
        reason="A small correction is needed.",
    )

    assert model_client.minute_action_prompts
    assert "A small correction is needed." in model_client.minute_action_prompts[-1]
    assert plan.minute_steps


@pytest.mark.anyio
async def test_hour_replan_planning_regenerates_remaining_time_blocks() -> None:
    model_client = PromptCapturingModelClient()
    planning = PlanningService(
        model_client=model_client,
        model_router=ModelRouter(
            settings=ModelRoutingSettings(
                decision=ModelRoute(
                    provider="custom",
                    model="decision-x",
                    base_url="https://mock/decision",
                )
            )
        ),
        memory_service=None,
    )

    plan = await planning.replan_after_completion(
        RuntimeState(
            plan=PlanState(
                plan_date="2026-03-23",
                day_summary="Keep the afternoon coherent.",
                current_hour_summary="Stay on the current lab thread.",
            )
        ),
        now=datetime(2026, 3, 23, 14, 0, tzinfo=UTC),
        kind=ReplanKind.HOUR_REPLAN,
        reason="A stronger thread emerged from the latest result.",
    )

    assert model_client.day_plan_prompts
    assert "A stronger thread emerged from the latest result." in model_client.day_plan_prompts[-1]
    assert plan.day_blocks
    assert plan.minute_steps
