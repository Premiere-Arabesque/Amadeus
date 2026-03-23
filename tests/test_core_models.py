from app.core.events import EventSource, EventType, RuntimeEvent
from app.core.outcomes import OutcomeStatus
from app.core.state import PlanStep, RuntimeState
from app.core.types import ExecutionMode


def test_runtime_event_defaults() -> None:
    event = RuntimeEvent(
        event_type=EventType.MESSAGE_RECEIVED,
        source=EventSource.USER,
        payload={"text": "hello"},
    )

    assert event.event_id.startswith("evt_")
    assert event.payload["text"] == "hello"


def test_runtime_state_defaults() -> None:
    state = RuntimeState()

    assert state.emotion.summary == "neutral"
    assert state.plan.minute_steps == []


def test_plan_step_and_mode_enums() -> None:
    step = PlanStep(title="Check messages", detail="Look at inbound user messages")

    assert step.minutes == 5
    assert OutcomeStatus.SUCCESS == "success"
    assert ExecutionMode.NARRATIVE == "narrative"
