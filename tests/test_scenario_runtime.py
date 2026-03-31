from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from app.communication.hub import CommunicationHub
from app.main import build_orchestrator, create_app
from app.memory.models import ActiveMemoryEntry
from app.runtime.clock import AdjustableClock
from app.runtime.scenario import ScenarioRunner, ScenarioStep
from tests.test_support import (
    InMemoryJsonlStore,
    MemoryHarness,
    build_in_memory_memory_service,
    build_in_memory_persona_service,
)


@pytest.mark.anyio
async def test_scenario_runner_can_advance_virtual_time_until_idle() -> None:
    memory_service, _ = build_in_memory_memory_service()
    communication_hub = CommunicationHub()
    orchestrator = build_orchestrator(
        communication_hub,
        memory_service,
        clock=AdjustableClock(
            start_at=datetime(2026, 3, 23, 14, 0, tzinfo=UTC),
            tick_real_time=False,
        ),
    )
    runner = ScenarioRunner(
        orchestrator=orchestrator,
        communication_hub=communication_hub,
    )

    result = await runner.replay(
        [
            ScenarioStep(action="run_once", label="boot"),
            ScenarioStep(action="advance_clock", minutes=10, label="fast-forward"),
            ScenarioStep(action="run_until_idle", max_iterations=10, label="catch-up"),
        ]
    )

    assert len(result.trace) == 3
    assert len(result.trace[0].outcomes) == 1
    assert result.trace[0].state.current_time == "2026-03-23T14:00:00+00:00"
    assert result.trace[2].clock_at == "2026-03-23T14:10:00+00:00"
    assert len(result.trace[2].outcomes) == 0
    assert result.state.next_step_scheduled_for == "2026-03-23T14:15:00+00:00"
    assert result.state.plan_summary.startswith("起床洗漱吃早饭；")
    assert result.state.active_day_summary.startswith("13:00-17:30")


def test_api_exposes_clock_control_and_scenario_replay() -> None:
    memory_service, _ = build_in_memory_memory_service()
    persona_service, _ = build_in_memory_persona_service()
    app = create_app(
        memory_service=memory_service,
        persona_service=persona_service,
        runtime_clock=AdjustableClock(
            start_at=datetime(2026, 3, 23, 14, 0, tzinfo=UTC),
            tick_real_time=False,
        ),
    )
    client = TestClient(app)

    set_response = client.post(
        "/api/runtime/clock/set",
        json={"at": "2026-03-23T14:00:00+00:00"},
    )
    advance_response = client.post(
        "/api/runtime/clock/advance",
        json={"minutes": 0, "run_once": True},
    )
    replay_response = client.post(
        "/api/runtime/scenario/run",
        json={
            "steps": [
                {"action": "advance_clock", "minutes": 10, "label": "fast-forward"},
                {"action": "run_until_idle", "label": "catch-up", "max_iterations": 10},
                {
                    "action": "enqueue_event",
                    "event_type": "message_received",
                    "source": "user",
                    "payload": {"text": "hello from scenario"},
                    "label": "inject-message",
                },
                {
                    "action": "run_until_idle",
                    "label": "handle-message",
                    "max_iterations": 10,
                },
            ]
        },
    )

    assert set_response.status_code == 200
    assert advance_response.status_code == 200
    assert replay_response.status_code == 200

    set_payload = set_response.json()
    advance_payload = advance_response.json()
    replay_payload = replay_response.json()

    assert set_payload["trace"][0]["clock_at"] == "2026-03-23T14:00:00+00:00"
    assert set_payload["state"]["clock_controllable"] is True
    assert advance_payload["trace"][1]["outcomes"][0]["status"] == "success"
    assert replay_payload["trace"][3]["outbound_messages"]
    assert replay_payload["state"]["plan_summary"] == "围绕新收到的消息调整短期计划。"
    assert replay_payload["state"]["next_step_scheduled_for"] == "2026-03-23T14:15:00+00:00"


@pytest.mark.anyio
async def test_runtime_crossing_midnight_generates_a_new_day_plan() -> None:
    previous_day_entry = ActiveMemoryEntry(
        created_at="2026-03-23T23:40:00+00:00",
        content="Finished the last useful task of the day and left notes behind.",
        source="Weak Real Zone",
    )
    memory_service, _ = build_in_memory_memory_service(
        harness=MemoryHarness(
            active_store=InMemoryJsonlStore([previous_day_entry.model_dump(mode="json")])
        )
    )
    communication_hub = CommunicationHub()
    orchestrator = build_orchestrator(
        communication_hub,
        memory_service,
        clock=AdjustableClock(
            start_at=datetime(2026, 3, 23, 23, 55, tzinfo=UTC),
            tick_real_time=False,
        ),
    )
    runner = ScenarioRunner(
        orchestrator=orchestrator,
        communication_hub=communication_hub,
    )

    result = await runner.replay(
        [
            ScenarioStep(action="run_once", label="late-night-boot"),
            ScenarioStep(action="advance_clock", minutes=10, label="cross-midnight"),
            ScenarioStep(action="run_once", label="day-start"),
        ]
    )

    assert result.trace[2].clock_at == "2026-03-24T00:05:00+00:00"
    assert orchestrator.state.plan.plan_date == "2026-03-24"
    assert result.trace[2].outcomes == []
    assert result.state.plan_summary.startswith("接住昨天留下的线索：")
    assert result.state.active_day_summary.startswith("08:00-09:30 接住昨天留下的线索：")
