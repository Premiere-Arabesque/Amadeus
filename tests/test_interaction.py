from datetime import UTC, datetime, timedelta

import pytest

from app.communication.hub import CommunicationHub
from app.core.events import EventSource, EventType, RuntimeEvent
from app.core.outcomes import ReplanDecision, ReplanKind
from app.core.state import PlanState, PlanStep, RuntimeState
from app.memory.models import ActiveMemoryEntry
from app.runtime.clock import AdjustableClock
from app.runtime.interaction import InteractionService
from app.runtime.orchestrator import OrchestratorServices, RuntimeOrchestrator
from app.runtime.roleplay_agent import RoleplayAgent
from tests.test_support import InMemoryJsonlStore, MemoryHarness, build_in_memory_memory_service


class StaticRoleplayAgent(RoleplayAgent):
    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.last_context_text = ""

    async def respond(self, **kwargs) -> str:
        raise AssertionError("Execution path should not be used in these interaction tests.")

    async def respond_to_interaction(
        self,
        *,
        context,
        state,
        event,
        channel_name: str,
        partner_name: str,
        message_text: str,
    ) -> str:
        del state, event, channel_name, partner_name, message_text
        self.last_context_text = context.render_for_roleplay()
        return self.reply


class NoopExecutionService:
    async def execute_step(self, *args, **kwargs):
        raise AssertionError("Message handling should not route into execution first.")


class NoopPlanningService:
    async def plan_next_window(self, *args, **kwargs):
        raise AssertionError("Message handling should not route into planning first.")


class StaticReplanService:
    async def decide(self, **kwargs) -> ReplanDecision:
        return ReplanDecision(kind=ReplanKind.NO_REPLAN, reason="")


class RecordingReplanService:
    def __init__(self, kind: ReplanKind = ReplanKind.NO_REPLAN) -> None:
        self.kind = kind
        self.calls: list[dict[str, object]] = []

    async def decide(self, **kwargs) -> ReplanDecision:
        self.calls.append(kwargs)
        return ReplanDecision(kind=self.kind, reason="cooldown")


class FixedClock:
    def __init__(self, now: datetime) -> None:
        self._now = now

    def now(self) -> datetime:
        return self._now


@pytest.mark.anyio
async def test_interaction_service_retrieves_same_partner_memories_before_reply() -> None:
    memory_service, _ = build_in_memory_memory_service(
        harness=MemoryHarness(
            active_store=InMemoryJsonlStore(
                [
                    ActiveMemoryEntry(
                        created_at="2026-03-23T12:00:00+00:00",
                        content="Mayuri asked about recalibrating the device last time.",
                        source="interaction",
                        interaction_partner="Mayuri",
                    ).model_dump(mode="json"),
                    ActiveMemoryEntry(
                        created_at="2026-03-23T12:10:00+00:00",
                        content="Luka asked about the paper summary.",
                        source="interaction",
                        interaction_partner="Luka",
                    ).model_dump(mode="json"),
                ]
            )
        ),
        semantic_query_embedder=lambda _: [1.0, 0.0],
    )
    roleplay_agent = StaticRoleplayAgent("I am here. Tell me the urgent part first.")
    service = InteractionService(
        memory_service=memory_service,
        roleplay_agent=roleplay_agent,
    )

    result = await service.execute_interaction(
        event=RuntimeEvent(
            event_type=EventType.MESSAGE_RECEIVED,
            source=EventSource.USER,
            payload={
                "channel": "wechat",
                "user_id": "user-1",
                "user_name": "Mayuri",
                "text": "Are you there? I need help.",
            },
        ),
        state=RuntimeState(persona_name="Kurisu"),
    )

    assert "Mayuri asked about recalibrating the device last time." in roleplay_agent.last_context_text
    assert "paper summary" not in roleplay_agent.last_context_text
    assert result.messages[0].content == "I am here. Tell me the urgent part first."
    assert "Mayuri: Are you there? I need help." in result.memory_content
    assert "Kurisu: I am here. Tell me the urgent part first." in result.memory_content
    persisted = memory_service.get_persisted_roleplay_agent_context()
    assert any(entry.kind == "interaction_record" for entry in persisted.entries)


@pytest.mark.anyio
async def test_orchestrator_handles_message_via_interaction_before_replan() -> None:
    memory_service, _ = build_in_memory_memory_service()
    roleplay_agent = StaticRoleplayAgent("I am here, tell me what happened.")
    orchestrator = RuntimeOrchestrator(
        services=OrchestratorServices(
            planning=NoopPlanningService(),
            execution=NoopExecutionService(),
            replan=StaticReplanService(),
            interaction=InteractionService(
                memory_service=memory_service,
                roleplay_agent=roleplay_agent,
            ),
            memory=memory_service,
            communication=CommunicationHub(),
        ),
        initial_state=RuntimeState(persona_name="Kurisu"),
        now_provider=FixedClock(datetime(2026, 3, 23, 14, 0, tzinfo=UTC)).now,
    )
    event = RuntimeEvent(
        event_type=EventType.MESSAGE_RECEIVED,
        source=EventSource.USER,
        payload={
            "channel": "wechat",
            "user_id": "user-1",
            "user_name": "Mayuri",
            "text": "Are you there? I am in a rush.",
        },
    )

    await orchestrator.enqueue(event)
    outcome = await orchestrator.run_once()

    assert outcome is not None
    assert outcome.content == "I am here, tell me what happened."
    assert orchestrator.services.communication.outbox[0].content == "I am here, tell me what happened."
    assert memory_service.active_entries[-1].interaction_partner == "Mayuri"
    assert "Are you there? I am in a rush." in memory_service.active_entries[-1].content
    assert orchestrator.state.interaction_cooldown_until is not None


@pytest.mark.anyio
async def test_interaction_cooldown_blocks_execution_until_timeout_and_then_replans() -> None:
    memory_service, _ = build_in_memory_memory_service()
    roleplay_agent = StaticRoleplayAgent("I am here. Go on.")
    replan = RecordingReplanService()
    clock = AdjustableClock(start_at=datetime(2026, 3, 23, 14, 0, tzinfo=UTC))
    orchestrator = RuntimeOrchestrator(
        services=OrchestratorServices(
            planning=NoopPlanningService(),
            execution=NoopExecutionService(),
            replan=replan,
            interaction=InteractionService(
                memory_service=memory_service,
                roleplay_agent=roleplay_agent,
            ),
            memory=memory_service,
            communication=CommunicationHub(),
        ),
        initial_state=RuntimeState(
            persona_name="Kurisu",
            plan=PlanState(
                plan_date="2026-03-23",
                minute_steps=[
                    PlanStep(
                        title="Resume daily task",
                        detail="",
                        scheduled_for="2026-03-23T14:00:00+00:00",
                    )
                ],
            ),
        ),
        clock=clock,
        interaction_cooldown_seconds=180,
    )

    await orchestrator.enqueue(
        RuntimeEvent(
            event_type=EventType.MESSAGE_RECEIVED,
            source=EventSource.USER,
            payload={
                "channel": "wechat",
                "user_id": "user-1",
                "user_name": "Mayuri",
                "text": "Ping",
            },
        )
    )
    first = await orchestrator.run_once()

    assert first is not None
    assert replan.calls == []
    assert orchestrator.state.interaction_cooldown_until == "2026-03-23T14:03:00+00:00"

    waiting = await orchestrator.run_once()
    assert waiting is None
    assert replan.calls == []

    clock.advance(timedelta(minutes=3))
    after_timeout = await orchestrator.run_once()

    assert after_timeout is not None
    assert len(replan.calls) == 1
    assert replan.calls[0]["event"].payload["reason"] == "interaction_cooldown_expired"
    assert orchestrator.state.interaction_cooldown_until is None


@pytest.mark.anyio
async def test_new_message_during_cooldown_resets_timeout() -> None:
    memory_service, _ = build_in_memory_memory_service()
    roleplay_agent = StaticRoleplayAgent("ok")
    replan = RecordingReplanService()
    clock = AdjustableClock(start_at=datetime(2026, 3, 23, 14, 0, tzinfo=UTC))
    orchestrator = RuntimeOrchestrator(
        services=OrchestratorServices(
            planning=NoopPlanningService(),
            execution=NoopExecutionService(),
            replan=replan,
            interaction=InteractionService(
                memory_service=memory_service,
                roleplay_agent=roleplay_agent,
            ),
            memory=memory_service,
            communication=CommunicationHub(),
        ),
        initial_state=RuntimeState(
            persona_name="Kurisu",
            plan=PlanState(plan_date="2026-03-23"),
        ),
        clock=clock,
        interaction_cooldown_seconds=180,
    )

    await orchestrator.enqueue(
        RuntimeEvent(
            event_type=EventType.MESSAGE_RECEIVED,
            source=EventSource.USER,
            payload={
                "channel": "wechat",
                "user_id": "user-1",
                "user_name": "Mayuri",
                "text": "first",
            },
        )
    )
    await orchestrator.run_once()
    first_deadline = datetime.fromisoformat(orchestrator.state.interaction_cooldown_until or "")

    clock.advance(timedelta(minutes=2))
    await orchestrator.enqueue(
        RuntimeEvent(
            event_type=EventType.MESSAGE_RECEIVED,
            source=EventSource.USER,
            payload={
                "channel": "wechat",
                "user_id": "user-1",
                "user_name": "Mayuri",
                "text": "second",
            },
        )
    )
    await orchestrator.run_once()
    second_deadline = datetime.fromisoformat(orchestrator.state.interaction_cooldown_until or "")

    assert second_deadline == datetime(2026, 3, 23, 14, 5, tzinfo=UTC)
    assert second_deadline > first_deadline

    clock.advance(timedelta(minutes=2))
    still_waiting = await orchestrator.run_once()
    assert still_waiting is None
    assert replan.calls == []

    clock.advance(timedelta(minutes=1))
    timed_out = await orchestrator.run_once()
    assert timed_out is not None
    assert len(replan.calls) == 1
