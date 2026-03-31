import asyncio
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from app.communication.hub import CommunicationHub
from app.core.events import EventSource, EventType, RuntimeEvent
from app.core.outcomes import OutcomeStatus
from app.mcp.builtins import register_builtin_capabilities
from app.mcp.registry import CapabilityRegistry
from app.memory.models import ActiveMemoryEntry
from app.runtime.clock import AdjustableClock
from app.runtime.execution import ExecutionService
from app.runtime.interaction import InteractionPolicy
from app.runtime.orchestrator import OrchestratorServices, RuntimeOrchestrator
from app.runtime.planning import PlanningService
from app.runtime.replan import ReplanService
from tests.test_support import (
    InMemoryJsonlStore,
    MemoryHarness,
    build_in_memory_memory_service,
)


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
    memory_service=None,
) -> RuntimeOrchestrator:
    if memory_service is None:
        memory_service, _ = build_in_memory_memory_service()
    return RuntimeOrchestrator(
        services=OrchestratorServices(
            planning=PlanningService(),
            execution=ExecutionService(capability_registry or CapabilityRegistry()),
            replan=ReplanService(),
            interaction=InteractionPolicy(memory_service=memory_service),
            memory=memory_service,
            communication=CommunicationHub(),
        ),
        now_provider=clock.now,
    )


async def wait_for_condition(
    predicate,
    *,
    timeout_seconds: float = 1.0,
    interval_seconds: float = 0.05,
) -> None:
    attempts = max(1, int(timeout_seconds / interval_seconds))
    for _ in range(attempts):
        if predicate():
            return
        await asyncio.sleep(interval_seconds)
    pytest.fail("Timed out while waiting for orchestrator background work to complete.")


@pytest.mark.anyio
async def test_orchestrator_creates_hour_plan_and_executes_first_due_step() -> None:
    clock = FakeClock(datetime(2026, 3, 23, 14, 0, tzinfo=UTC))
    orchestrator = build_orchestrator(clock)

    outcomes = await orchestrator.run_ready()

    assert len(outcomes) == 1
    assert outcomes[0].status == OutcomeStatus.SUCCESS
    assert orchestrator.state.plan.hour_starts_at == "2026-03-23T14:00:00+00:00"
    assert orchestrator.state.plan.day_plan_items
    assert orchestrator.state.plan.active_day_item_id is not None
    assert orchestrator.state.plan.hour_plan_items
    assert orchestrator.state.plan.active_hour_item_id is not None
    assert orchestrator.state.plan.minute_steps[0].status == "complete"
    assert orchestrator.state.plan.minute_steps[1].status == "pending"
    assert orchestrator.state.current_action_id == orchestrator.state.plan.minute_steps[0].step_id
    assert orchestrator.services.communication.outbox == []


@pytest.mark.anyio
async def test_orchestrator_follows_scheduled_steps_without_heartbeat() -> None:
    clock = FakeClock(datetime(2026, 3, 23, 14, 0, tzinfo=UTC))
    orchestrator = build_orchestrator(clock)

    await orchestrator.run_ready()
    first_day_item_id = orchestrator.state.plan.active_day_item_id

    clock.advance(minutes=4)
    outcomes = await orchestrator.run_ready()
    assert outcomes == []

    clock.advance(minutes=11)
    outcomes = await orchestrator.run_ready()
    assert len(outcomes) == 1
    assert orchestrator.state.plan.minute_steps[1].status == "complete"

    clock.advance(minutes=15)
    outcomes = await orchestrator.run_ready()
    assert len(outcomes) == 1
    assert orchestrator.state.plan.active_day_item_id != first_day_item_id
    assert orchestrator.state.plan.active_hour_item_id is None
    assert orchestrator.state.plan.hour_plan_items == []
    assert orchestrator.state.plan.minute_steps == []
    assert orchestrator.next_wake_at() == datetime(2026, 3, 23, 17, 0, tzinfo=UTC)


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
        "围绕新收到的消息调整短期计划。"
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
        assert "Example Paper" in outcome.content
        assert "Quantum bananas" in outcome.raw_data["result"]["content"]
        assert "提炼到的重点" in orchestrator.services.communication.outbox[0].content
        assert orchestrator.state.plan.minute_steps[0].capability == "read_url"
        assert orchestrator.state.plan.minute_steps[0].status == "complete"
        assert orchestrator.state.plan.minute_steps[1].title == "整理有用结论"
    finally:
        await read_url_client.aclose()


@pytest.mark.anyio
async def test_message_interaction_retrieves_same_partner_memory_and_tags_new_memory() -> None:
    clock = FakeClock(datetime(2026, 3, 23, 14, 0, tzinfo=UTC))
    memory_service, _ = build_in_memory_memory_service(
        harness=MemoryHarness(
            active_store=InMemoryJsonlStore(
                [
                    ActiveMemoryEntry(
                        created_at="2026-03-23T12:00:00+00:00",
                        content="We already discussed the banana experiment calibration steps.",
                        source="interaction",
                        interaction_partner="Mayuri",
                    ).model_dump(mode="json"),
                    ActiveMemoryEntry(
                        created_at="2026-03-23T12:10:00+00:00",
                        content="A different user asked about a paper summary.",
                        source="interaction",
                        interaction_partner="Luka",
                    ).model_dump(mode="json"),
                ]
            )
        )
    )
    orchestrator = build_orchestrator(clock, memory_service=memory_service)

    await orchestrator.enqueue(
        RuntimeEvent(
            event_type=EventType.MESSAGE_RECEIVED,
            source=EventSource.USER,
            created_at=clock.now().isoformat(),
            payload={
                "user_id": "user-1",
                "user_name": "Mayuri",
                "channel": "api",
                "text": "Can you continue the banana experiment thread?",
            },
        )
    )

    outcome = await orchestrator.run_once()

    assert outcome is not None
    message = orchestrator.services.communication.outbox[0]
    assert "banana experiment calibration steps" in message.content
    assert "different user asked about a paper summary" not in message.content.lower()
    assert memory_service.active_entries[-1].interaction_partner == "Mayuri"


@pytest.mark.anyio
async def test_background_scheduler_triggers_day_start_at_midnight() -> None:
    memory_service, _ = build_in_memory_memory_service()
    orchestrator = RuntimeOrchestrator(
        services=OrchestratorServices(
            planning=PlanningService(),
            execution=ExecutionService(CapabilityRegistry()),
            replan=ReplanService(),
            interaction=InteractionPolicy(memory_service=memory_service),
            memory=memory_service,
            communication=CommunicationHub(),
        ),
        clock=AdjustableClock(
            start_at=datetime(2026, 3, 23, 23, 59, 58, tzinfo=UTC),
            tick_real_time=True,
        ),
    )

    try:
        await orchestrator.start_scheduler()
        await asyncio.sleep(2.2)
        assert orchestrator.state.plan.plan_date == "2026-03-24"
        assert orchestrator.state.plan.day_blocks
        assert orchestrator.state.plan.minute_steps == []
    finally:
        await orchestrator.stop_scheduler()


@pytest.mark.anyio
async def test_background_scheduler_processes_enqueued_event_without_explicit_run_once() -> None:
    memory_service, _ = build_in_memory_memory_service()
    orchestrator = RuntimeOrchestrator(
        services=OrchestratorServices(
            planning=PlanningService(),
            execution=ExecutionService(CapabilityRegistry()),
            replan=ReplanService(),
            interaction=InteractionPolicy(memory_service=memory_service),
            memory=memory_service,
            communication=CommunicationHub(),
        ),
        clock=AdjustableClock(
            start_at=datetime(2026, 3, 23, 14, 0, tzinfo=UTC),
            tick_real_time=True,
        ),
    )

    try:
        await orchestrator.start_scheduler()
        await wait_for_condition(lambda: orchestrator.state.current_action_id is not None)
        orchestrator.services.communication.drain_outbox()

        event = RuntimeEvent(
            event_type=EventType.MESSAGE_RECEIVED,
            source=EventSource.USER,
            created_at=orchestrator.now().isoformat(),
            payload={
                "user_id": "user-1",
                "user_name": "Mayuri",
                "channel": "api",
                "text": "hello from the background loop",
            },
        )
        await orchestrator.enqueue(event)

        await wait_for_condition(lambda: event.event_id not in orchestrator.state.pending_event_ids)

        assert orchestrator.services.communication.outbox
        assert orchestrator.state.plan.day_summary == "围绕新收到的消息调整短期计划。"
        assert "background loop" in orchestrator.services.communication.outbox[0].content
    finally:
        await orchestrator.stop_scheduler()
