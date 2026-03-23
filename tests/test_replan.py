from datetime import UTC, datetime, timedelta

import pytest

from app.communication.hub import CommunicationHub
from app.core.outcomes import ActionOutcome, OutcomeStatus, ReplanKind
from app.core.state import PlanState, PlanStep, RuntimeState
from app.core.types import ExecutionMode
from app.runtime.emotion import EmotionService
from app.runtime.execution import ExecutionService
from app.runtime.interaction import InteractionPolicy
from app.runtime.orchestrator import OrchestratorServices, RuntimeOrchestrator
from app.runtime.planning import PlanningService
from app.runtime.replan import ReplanService


class FakeClock:
    def __init__(self, now: datetime) -> None:
        self._now = now

    def now(self) -> datetime:
        return self._now

    def advance(self, *, minutes: int = 0) -> None:
        self._now += timedelta(minutes=minutes)


class InMemoryMemoryService:
    def __init__(self) -> None:
        self.raw_events = []
        self.outcomes = []
        self.snapshots = []

    async def append_raw_event(self, event) -> None:
        self.raw_events.append(event)

    async def record_outcome(self, outcome) -> None:
        self.outcomes.append(outcome)

    async def save_snapshot(self, state) -> None:
        self.snapshots.append(state.model_copy(deep=True))


class ScriptedExecutionService:
    def __init__(self, outcome_status: OutcomeStatus) -> None:
        self.outcome_status = outcome_status

    async def execute_step(self, step: PlanStep, state: RuntimeState) -> ActionOutcome:
        del state
        return ActionOutcome(
            action_id=step.step_id,
            status=self.outcome_status,
            mode=step.execution_mode,
            summary=f"Scripted outcome for {step.title}",
            raw={"detail": step.detail},
        )


@pytest.mark.anyio
async def test_replan_service_requests_micro_replan_when_window_is_exhausted() -> None:
    service = ReplanService()
    state = RuntimeState(
        plan=PlanState(
            minute_steps=[
                PlanStep(
                    title="Complete the current block",
                    detail="Already done.",
                    status="complete",
                )
            ]
        )
    )
    outcome = ActionOutcome(
        action_id="step_1",
        status=OutcomeStatus.SUCCESS,
        mode=ExecutionMode.NARRATIVE,
        summary="Completed the current block.",
    )

    decision = await service.decide(state, outcome)

    assert decision.kind == ReplanKind.MICRO_REPLAN


@pytest.mark.anyio
async def test_orchestrator_applies_micro_replan_after_exhausting_the_current_window() -> None:
    clock = FakeClock(datetime(2026, 3, 23, 14, 0, tzinfo=UTC))
    memory = InMemoryMemoryService()
    orchestrator = RuntimeOrchestrator(
        services=OrchestratorServices(
            planning=PlanningService(),
            execution=ExecutionService(),
            emotion=EmotionService(),
            replan=ReplanService(),
            interaction=InteractionPolicy(),
            memory=memory,
            communication=CommunicationHub(),
        ),
        initial_state=RuntimeState(
            plan=PlanState(
                day_summary="Short window before replan",
                current_hour_summary="Finish the current block.",
                hour_starts_at="2026-03-23T14:00:00+00:00",
                minute_steps=[
                    PlanStep(
                        title="Finish the last step in the window",
                        detail="Wrap the current short block.",
                        minutes=5,
                        scheduled_for="2026-03-23T14:00:00+00:00",
                    )
                ],
            )
        ),
        now_provider=clock.now,
    )

    outcomes = await orchestrator.run_ready()

    assert len(outcomes) == 1
    assert outcomes[0].status == OutcomeStatus.SUCCESS
    assert orchestrator.state.plan.day_summary == (
        "Refresh the next short planning window after the previous step completed."
    )
    assert orchestrator.state.plan.minute_steps[0].scheduled_for == "2026-03-23T14:05:00+00:00"
    assert orchestrator.state.plan.minute_steps[0].status == "pending"
    assert memory.snapshots


@pytest.mark.anyio
async def test_orchestrator_applies_recovery_replan_after_failed_step() -> None:
    clock = FakeClock(datetime(2026, 3, 23, 14, 0, tzinfo=UTC))
    memory = InMemoryMemoryService()
    orchestrator = RuntimeOrchestrator(
        services=OrchestratorServices(
            planning=PlanningService(),
            execution=ScriptedExecutionService(OutcomeStatus.BLOCKED_FAILURE),
            emotion=EmotionService(),
            replan=ReplanService(),
            interaction=InteractionPolicy(),
            memory=memory,
            communication=CommunicationHub(),
        ),
        initial_state=RuntimeState(
            plan=PlanState(
                day_summary="Short window before recovery replan",
                current_hour_summary="Run one tool step.",
                hour_starts_at="2026-03-23T14:00:00+00:00",
                minute_steps=[
                    PlanStep(
                        title="Search the web",
                        detail="Try to fetch outside information.",
                        minutes=5,
                        execution_mode=ExecutionMode.TOOL,
                        capability="search_web",
                        arguments={"query": "unstable result"},
                        scheduled_for="2026-03-23T14:00:00+00:00",
                    )
                ],
            )
        ),
        now_provider=clock.now,
    )

    outcomes = await orchestrator.run_ready()

    assert len(outcomes) == 1
    assert outcomes[0].status == OutcomeStatus.BLOCKED_FAILURE
    assert orchestrator.state.plan.day_summary == (
        "Adjust the short-term plan after the previous step underperformed."
    )
    assert orchestrator.state.plan.minute_steps[0].title == "Stabilize after the failed step"
    assert orchestrator.state.plan.minute_steps[0].scheduled_for == "2026-03-23T14:05:00+00:00"
    assert memory.snapshots
