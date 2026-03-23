from datetime import UTC, datetime

import pytest

from app.core.events import EventSource, EventType, RuntimeEvent
from app.core.state import RuntimeState
from app.core.types import ExecutionMode
from app.runtime.planning import PlanningService


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
