from datetime import UTC, datetime, timedelta

import pytest

from app.communication.hub import CommunicationHub
from app.core.outcomes import (
    ActionOutcome,
    ExecutionTraceEntry,
    OutcomeStatus,
    ReplanDecision,
    ReplanKind,
)
from app.core.state import PlanState, PlanStep, RuntimeState
from app.core.types import ExecutionMode, ExecutionZone
from app.runtime.clock import AdjustableClock
from app.runtime.contact_book import ContactBook, ContactEntry
from app.runtime.interaction import InteractionService
from app.runtime.orchestrator import OrchestratorServices, RuntimeOrchestrator
from app.runtime.roleplay_agent import RoleplayAgent
from tests.test_support import build_in_memory_memory_service


class NoopRoleplayAgent(RoleplayAgent):
    async def respond(self, **kwargs) -> str:
        raise AssertionError("Execution roleplay path should not be used in this test.")

    async def respond_to_interaction(self, **kwargs) -> str:
        raise AssertionError("Inbound interaction path should not be used in this test.")


class ProactiveExecutionService:
    async def execute_step(self, step, *, state, event=None, loop_context=None):
        del state, event, loop_context
        return ActionOutcome(
            action_id=step.step_id,
            status=OutcomeStatus.SUCCESS,
            mode=ExecutionMode.NARRATIVE,
            source=ExecutionZone.NON_REAL,
            content="The role decided to reach out to Mayuri.",
            execution_trace=[
                ExecutionTraceEntry(stage="loop_stop", content="proactive_interaction"),
            ],
            raw_data={
                "loop_stop_reason": "proactive_interaction",
                "proactive_interaction": {
                    "name": "Mayuri",
                    "message_content": "Do you have a minute? I want to ask you something.",
                },
            },
        )


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


class NoopPlanningService:
    def __init__(self) -> None:
        self.advance_calls = 0

    async def plan_next_window(self, *args, **kwargs):
        raise AssertionError("Planning path should not be used in this test.")

    async def advance_after_completion(self, state, *, now):
        del now
        self.advance_calls += 1
        return state.plan


class FixedClock:
    def __init__(self, now: datetime) -> None:
        self._now = now

    def now(self) -> datetime:
        return self._now


@pytest.mark.anyio
async def test_outbound_interaction_sends_first_roleplay_message_and_persists_context() -> None:
    memory_service, _ = build_in_memory_memory_service()
    contact_book = ContactBook(
        contacts=[
            ContactEntry(name="Mayuri", recipient_id="user-1", channel="wechat"),
        ]
    )
    service = InteractionService(
        memory_service=memory_service,
        roleplay_agent=NoopRoleplayAgent(),
        contact_book=contact_book,
    )

    result = await service.execute_outbound_interaction(
        state=RuntimeState(persona_name="Kurisu"),
        partner_name="Mayuri",
        message_text="Do you have a minute? I want to ask you something.",
    )

    assert result.messages[0].recipient_id == "user-1"
    assert result.messages[0].channel == "wechat"
    assert result.messages[0].content == "Do you have a minute? I want to ask you something."
    assert "Mayuri" in result.memory_content
    assert "Do you have a minute? I want to ask you something." in result.memory_content
    persisted = memory_service.get_persisted_roleplay_agent_context()
    assert any("Mayuri" in entry.content for entry in persisted.entries)


@pytest.mark.anyio
async def test_orchestrator_hands_off_execution_into_proactive_interaction() -> None:
    memory_service, _ = build_in_memory_memory_service()
    contact_book = ContactBook(
        contacts=[
            ContactEntry(name="Mayuri", recipient_id="user-1", channel="wechat"),
        ]
    )
    orchestrator = RuntimeOrchestrator(
        services=OrchestratorServices(
            planning=NoopPlanningService(),
            execution=ProactiveExecutionService(),
            replan=StaticReplanService(),
            interaction=InteractionService(
                memory_service=memory_service,
                roleplay_agent=NoopRoleplayAgent(),
                contact_book=contact_book,
            ),
            memory=memory_service,
            communication=CommunicationHub(),
        ),
        initial_state=RuntimeState(
            persona_name="Kurisu",
            plan=PlanState(
                plan_date="2026-04-05",
                minute_steps=[
                    PlanStep(
                        title="Handle messages",
                        detail="",
                        scheduled_for="2026-04-05T10:00:00+00:00",
                    )
                ],
            ),
        ),
        now_provider=FixedClock(datetime(2026, 4, 5, 10, 0, tzinfo=UTC)).now,
    )

    outcome = await orchestrator.run_once()

    assert outcome is not None
    assert outcome.content == "Do you have a minute? I want to ask you something."
    assert orchestrator.services.communication.outbox[0].content == "Do you have a minute? I want to ask you something."
    assert memory_service.active_entries[-1].interaction_partner == "Mayuri"
    assert orchestrator.state.interaction_cooldown_until is not None


@pytest.mark.anyio
async def test_outbound_interaction_enters_cooldown_and_replans_after_timeout() -> None:
    memory_service, _ = build_in_memory_memory_service()
    contact_book = ContactBook(
        contacts=[
            ContactEntry(name="Mayuri", recipient_id="user-1", channel="wechat"),
        ]
    )
    planning = NoopPlanningService()
    replan = RecordingReplanService()
    clock = AdjustableClock(start_at=datetime(2026, 4, 5, 10, 0, tzinfo=UTC))
    orchestrator = RuntimeOrchestrator(
        services=OrchestratorServices(
            planning=planning,
            execution=ProactiveExecutionService(),
            replan=replan,
            interaction=InteractionService(
                memory_service=memory_service,
                roleplay_agent=NoopRoleplayAgent(),
                contact_book=contact_book,
            ),
            memory=memory_service,
            communication=CommunicationHub(),
        ),
        initial_state=RuntimeState(
            persona_name="Kurisu",
            plan=PlanState(
                plan_date="2026-04-05",
                minute_steps=[
                    PlanStep(
                        title="Handle messages",
                        detail="",
                        scheduled_for="2026-04-05T10:00:00+00:00",
                    )
                ],
            ),
        ),
        clock=clock,
        interaction_cooldown_seconds=180,
    )

    first = await orchestrator.run_once()

    assert first is not None
    assert replan.calls == []
    assert orchestrator.state.interaction_cooldown_until == "2026-04-05T10:03:00+00:00"

    clock.advance(timedelta(minutes=3))
    after_timeout = await orchestrator.run_once()

    assert after_timeout is not None
    assert len(replan.calls) == 1
    assert replan.calls[0]["event"].payload["reason"] == "interaction_cooldown_expired"
    assert planning.advance_calls == 1
    assert orchestrator.state.interaction_cooldown_until is None
