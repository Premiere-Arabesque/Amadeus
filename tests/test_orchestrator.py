from datetime import UTC, datetime, timedelta

import httpx
import pytest

from app.communication.hub import CommunicationHub
from app.core.events import EventSource, EventType, RuntimeEvent
from app.core.outcomes import OutcomeStatus
from app.mcp.builtins import register_builtin_capabilities
from app.mcp.compat import MCPCompatLayer
from app.mcp.registry import CapabilityRegistry
from app.runtime.emotion import EmotionService
from app.runtime.execution import ExecutionService
from app.runtime.interaction import InteractionPolicy
from app.runtime.orchestrator import OrchestratorServices, RuntimeOrchestrator
from app.runtime.planning import PlanningService
from app.runtime.replan import ReplanService
from tests.test_support import build_in_memory_memory_service


class FakeClock:
    def __init__(self, now: datetime) -> None:
        self._now = now

    def now(self) -> datetime:
        return self._now

    def advance(self, *, minutes: int = 0) -> None:
        self._now += timedelta(minutes=minutes)


def build_orchestrator(
    clock: FakeClock,
    *,
    capability_registry: CapabilityRegistry | None = None,
) -> RuntimeOrchestrator:
    memory_service, _ = build_in_memory_memory_service()
    return RuntimeOrchestrator(
        services=OrchestratorServices(
            planning=PlanningService(),
            execution=ExecutionService(
                MCPCompatLayer(capability_registry or CapabilityRegistry())
            ),
            emotion=EmotionService(),
            replan=ReplanService(),
            interaction=InteractionPolicy(),
            memory=memory_service,
            communication=CommunicationHub(),
        ),
        now_provider=clock.now,
    )


@pytest.mark.anyio
async def test_orchestrator_creates_hour_plan_and_executes_first_due_step() -> None:
    clock = FakeClock(datetime(2026, 3, 23, 14, 0, tzinfo=UTC))
    orchestrator = build_orchestrator(clock)

    outcomes = await orchestrator.run_ready()

    assert len(outcomes) == 1
    assert outcomes[0].status == OutcomeStatus.SUCCESS
    assert orchestrator.state.plan.hour_starts_at == "2026-03-23T14:00:00+00:00"
    assert orchestrator.state.plan.minute_steps[0].status == "complete"
    assert orchestrator.state.plan.minute_steps[1].status == "pending"
    assert orchestrator.state.current_action_id == orchestrator.state.plan.minute_steps[0].step_id
    assert orchestrator.services.communication.outbox == []


@pytest.mark.anyio
async def test_orchestrator_follows_scheduled_steps_without_heartbeat() -> None:
    clock = FakeClock(datetime(2026, 3, 23, 14, 0, tzinfo=UTC))
    orchestrator = build_orchestrator(clock)

    await orchestrator.run_ready()

    clock.advance(minutes=4)
    outcomes = await orchestrator.run_ready()
    assert outcomes == []

    clock.advance(minutes=1)
    outcomes = await orchestrator.run_ready()
    assert len(outcomes) == 1
    assert orchestrator.state.plan.minute_steps[1].status == "complete"

    clock.advance(minutes=5)
    outcomes = await orchestrator.run_ready()
    assert len(outcomes) == 1
    assert orchestrator.state.plan.day_summary == (
        "Refresh the next short planning window after the previous step completed."
    )
    assert orchestrator.state.plan.minute_steps[0].scheduled_for == "2026-03-23T14:15:00+00:00"
    assert orchestrator.state.plan.minute_steps[0].status == "pending"
    assert orchestrator.next_wake_at() == datetime(2026, 3, 23, 14, 15, tzinfo=UTC)


@pytest.mark.anyio
async def test_message_interrupt_replaces_current_plan_immediately() -> None:
    clock = FakeClock(datetime(2026, 3, 23, 14, 0, tzinfo=UTC))
    orchestrator = build_orchestrator(clock)

    await orchestrator.run_ready()
    prior_plan_step_ids = [step.step_id for step in orchestrator.state.plan.minute_steps]

    clock.advance(minutes=2)
    await orchestrator.enqueue(
        RuntimeEvent(
            event_type=EventType.MESSAGE_RECEIVED,
            source=EventSource.USER,
            created_at=clock.now().isoformat(),
            payload={"text": "hello"},
        )
    )

    outcome = await orchestrator.run_once()

    assert outcome is not None
    assert outcome.status == OutcomeStatus.SUCCESS
    assert orchestrator.services.communication.outbox
    assert orchestrator.state.plan.day_summary == (
        "Adapt the short-term plan around the new inbound message."
    )
    assert orchestrator.state.plan.minute_steps[0].scheduled_for == "2026-03-23T14:02:00+00:00"
    assert orchestrator.state.plan.minute_steps[0].status == "complete"
    assert orchestrator.state.plan.minute_steps[1].scheduled_for == "2026-03-23T14:07:00+00:00"
    assert prior_plan_step_ids != [step.step_id for step in orchestrator.state.plan.minute_steps]


@pytest.mark.anyio
async def test_message_with_url_executes_read_url_tool_step() -> None:
    clock = FakeClock(datetime(2026, 3, 23, 14, 0, tzinfo=UTC))

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://example.com/paper"
        return httpx.Response(
            status_code=200,
            headers={"content-type": "text/html; charset=utf-8"},
            text=(
                "<html><head><title>Example Paper</title></head>"
                "<body><p>Quantum bananas improve time travel stability.</p></body></html>"
            ),
        )

    read_url_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    registry = register_builtin_capabilities(
        CapabilityRegistry(),
        read_url_http_client=read_url_client,
    )
    orchestrator = build_orchestrator(
        clock,
        capability_registry=registry,
    )

    try:
        await orchestrator.enqueue(
            RuntimeEvent(
                event_type=EventType.MESSAGE_RECEIVED,
                source=EventSource.USER,
                created_at=clock.now().isoformat(),
                payload={"text": "Please read https://example.com/paper"},
            )
        )

        outcome = await orchestrator.run_once()

        assert outcome is not None
        assert outcome.status == OutcomeStatus.SUCCESS
        assert outcome.mode == "hybrid"
        assert outcome.tool_invocations[0].capability == "read_url"
        assert "Example Paper" in outcome.summary
        assert "Quantum bananas" in outcome.raw["result"]["content"]
        assert "Key point" in orchestrator.services.communication.outbox[0].content
        assert orchestrator.state.plan.minute_steps[0].capability == "read_url"
        assert orchestrator.state.plan.minute_steps[0].status == "complete"
        assert orchestrator.state.plan.minute_steps[1].title == "Capture the useful takeaway"
    finally:
        await read_url_client.aclose()
