from datetime import UTC, datetime, timedelta

import pytest

from app.communication.hub import CommunicationHub
from app.core.events import EventSource, EventType, RuntimeEvent
from app.core.outcomes import ActionOutcome, OutcomeStatus, ReplanDecision, ReplanKind
from app.core.state import PlanOutlineItem, PlanOutlineStatus, PlanState, PlanStep, RuntimeState
from app.core.types import ExecutionMode, ExecutionZone
from app.infra.model_client import ModelClient, ModelRequest, ModelRouter, StructuredResponse
from app.infra.settings import ModelRoute, ModelRoutingSettings
from app.memory.models import ActiveMemoryEntry
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


class ScriptedExecutionService:
    def __init__(self, status: OutcomeStatus) -> None:
        self.status = status

    async def execute_step(
        self,
        step: PlanStep,
        *,
        state: RuntimeState,
        event: RuntimeEvent | None = None,
        loop_context=None,
    ) -> ActionOutcome:
        del state, event, loop_context
        return ActionOutcome(
            action_id=step.step_id,
            status=self.status,
            mode=step.execution_mode,
            source=step.zone_hint,
            content=f"Scripted outcome for {step.title}",
            raw_data={"detail": step.detail},
        )


class ScriptedReplanService:
    def __init__(self, decision: ReplanDecision) -> None:
        self.decision = decision

    async def decide(
        self,
        *,
        now: datetime | None = None,
        state: RuntimeState | None,
        event: RuntimeEvent | None,
        outcome: ActionOutcome,
        plan_exhausted: bool = False,
    ) -> ReplanDecision:
        del now, state, event, outcome, plan_exhausted
        return self.decision


class PromptCapturingModelClient(ModelClient):
    def __init__(
        self,
        *,
        kind: ReplanKind,
        reason: str,
    ) -> None:
        self.kind = kind
        self.reason = reason
        self.prompts: list[str] = []
        self.system_prompts: list[str] = []
        self.structured_requests: list[ModelRequest] = []

    async def generate_text(self, request: ModelRequest):
        raise AssertionError(f"generate_text should not be called: {request}")

    async def generate_structured(self, request: ModelRequest, schema_type):
        self.prompts.append(request.prompt)
        self.system_prompts.append(request.system_prompt)
        self.structured_requests.append(request)
        return StructuredResponse(
            structured=schema_type.model_validate(
                {
                    "decision": self.kind.value,
                    "reason": self.reason,
                }
            )
        )


def build_orchestrator(
    clock: FakeClock,
    *,
    execution,
    replan,
    initial_state: RuntimeState,
):
    memory_service, _ = build_in_memory_memory_service()
    return (
        RuntimeOrchestrator(
            services=OrchestratorServices(
                planning=PlanningService(memory_service=memory_service),
                execution=execution,
                replan=replan,
                interaction=InteractionPolicy(memory_service=memory_service),
                memory=memory_service,
                communication=CommunicationHub(),
            ),
            initial_state=initial_state,
            now_provider=clock.now,
        ),
        memory_service,
    )


def configured_replan_router() -> ModelRouter:
    return ModelRouter(
        settings=ModelRoutingSettings(
            decision=ModelRoute(
                provider="custom",
                model="decision-x",
                base_url="https://mock/decision",
            )
        )
    )


@pytest.mark.anyio
async def test_replan_service_uses_relevant_memory_context_in_model_prompt() -> None:
    memory_service, _ = build_in_memory_memory_service(
        harness=MemoryHarness(
            active_store=InMemoryJsonlStore(
                [
                    ActiveMemoryEntry(
                        created_at="2026-03-23T13:40:00+00:00",
                        content=(
                            "We already had to recalibrate the spectrometer after drifting "
                            "baseline noise."
                        ),
                        source="Weak Real Zone",
                    ).model_dump(mode="json")
                ]
            )
        )
    )
    memory_service.update_persona_context(
        soul_md="# 灵魂档案：Kurisu\n\n## 核心设定\nLab assistant keeping the experiment coherent.",
    )
    memory_service.update_plan_context(
        plan_summary="Keep the afternoon experiment stable.",
        plan_date="2026-03-23",
    )
    model_client = PromptCapturingModelClient(
        kind=ReplanKind.MICRO_REPLAN,
        reason="The current hour needs a small correction.",
    )
    service = ReplanService(
        model_client=model_client,
        model_router=configured_replan_router(),
        memory_service=memory_service,
    )

    decision = await service.decide(
        state=RuntimeState(
            persona_summary="Lab assistant keeping the experiment coherent.",
            plan=PlanState(
                day_summary="Keep the afternoon experiment stable.",
                current_hour_summary="Calibrate the spectrometer carefully.",
            ),
        ),
        event=RuntimeEvent(
            event_type=EventType.MESSAGE_RECEIVED,
            source=EventSource.USER,
            payload={"text": "The spectrometer is drifting again."},
        ),
        outcome=ActionOutcome(
            action_id="step_1",
            status=OutcomeStatus.BLOCKED_FAILURE,
            mode=ExecutionMode.NARRATIVE,
            source=ExecutionZone.WEAK_REAL,
            content="The spectrometer check stalled after inconsistent readings.",
        ),
        plan_exhausted=False,
    )

    prompt = model_client.prompts[-1]
    system_prompt = model_client.system_prompts[-1]

    assert decision.kind == ReplanKind.MICRO_REPLAN
    assert len(model_client.structured_requests) == 1
    assert "spectrometer check stalled after inconsistent readings" in prompt
    assert "We already had to recalibrate the spectrometer" in prompt
    assert "The spectrometer is drifting again." in prompt
    assert "# 灵魂档案：Kurisu" in system_prompt
    assert "Keep the afternoon experiment stable." in system_prompt


@pytest.mark.anyio
async def test_replan_service_returns_no_replan_from_single_structured_call() -> None:
    model_client = PromptCapturingModelClient(
        kind=ReplanKind.NO_REPLAN,
        reason="The interruption was harmless and the plan can continue.",
    )
    service = ReplanService(
        model_client=model_client,
        model_router=configured_replan_router(),
        memory_service=None,
    )

    decision = await service.decide(
        state=RuntimeState(),
        event=None,
        outcome=ActionOutcome(
            action_id="step_no_replan",
            status=OutcomeStatus.BLOCKED_FAILURE,
            mode=ExecutionMode.NARRATIVE,
            source=ExecutionZone.WEAK_REAL,
            content="The interruption was harmless and the plan can continue.",
        ),
    )

    assert decision.kind == ReplanKind.NO_REPLAN
    assert decision.reason == "The interruption was harmless and the plan can continue."
    assert len(model_client.structured_requests) == 1


@pytest.mark.anyio
async def test_failed_step_triggers_micro_replan_even_when_future_steps_remain() -> None:
    clock = FakeClock(datetime(2026, 3, 23, 14, 0, tzinfo=UTC))
    initial_state = RuntimeState(
        plan=PlanState(
            plan_date="2026-03-23",
            day_summary="Keep the lab work coherent.",
            current_hour_summary="Calibrate the spectrometer.",
            hour_starts_at="2026-03-23T14:00:00+00:00",
            minute_steps=[
                PlanStep(
                    title="Check the spectrometer",
                    detail="Run the next calibration pass.",
                    scheduled_for="2026-03-23T14:00:00+00:00",
                ),
                PlanStep(
                    title="Record the next reading",
                    detail="Write down the follow-up measurement.",
                    scheduled_for="2026-03-23T14:05:00+00:00",
                ),
            ],
        )
    )
    original_step_ids = [step.step_id for step in initial_state.plan.minute_steps]
    orchestrator, memory_service = build_orchestrator(
        clock,
        execution=ScriptedExecutionService(OutcomeStatus.BLOCKED_FAILURE),
        replan=ReplanService(memory_service=None),
        initial_state=initial_state,
    )

    outcomes = await orchestrator.run_ready()

    assert len(outcomes) == 1
    assert outcomes[0].status == OutcomeStatus.BLOCKED_FAILURE
    assert orchestrator.state.plan.minute_steps[0].detail.startswith("先进入这个时段")
    assert orchestrator.state.plan.minute_steps[0].scheduled_for == "2026-03-23T14:00:00+00:00"
    assert original_step_ids[1] not in [
        step.step_id for step in orchestrator.state.plan.minute_steps
    ]

    replan_entries = [entry for entry in memory_service.raw_entries if entry.kind == "replan"]
    assert replan_entries
    assert replan_entries[-1].payload["decision"]["kind"] == "micro_replan"


@pytest.mark.anyio
async def test_exhausted_window_advances_even_if_replan_decision_is_no_replan() -> None:
    clock = FakeClock(datetime(2026, 3, 23, 14, 0, tzinfo=UTC))
    first_hour = PlanOutlineItem(
        summary="Finish the current block.",
        status=PlanOutlineStatus.ACTIVE,
    )
    second_hour = PlanOutlineItem(summary="Shift into the next block.")
    initial_state = RuntimeState(
        plan=PlanState(
            plan_date="2026-03-23",
            day_summary="Keep the afternoon coherent.",
            day_plan_items=[
                PlanOutlineItem(
                    summary="Push the main thread forward.",
                    status=PlanOutlineStatus.ACTIVE,
                )
            ],
            current_hour_summary=first_hour.summary,
            active_day_item_id=None,
            hour_plan_items=[first_hour, second_hour],
            active_hour_item_id=first_hour.item_id,
            hour_starts_at="2026-03-23T14:00:00+00:00",
            minute_steps=[
                PlanStep(
                    title="Finish the current block",
                    detail="Wrap the last minute step in this block.",
                    scheduled_for="2026-03-23T14:00:00+00:00",
                )
            ],
        )
    )
    initial_state.plan.active_day_item_id = initial_state.plan.day_plan_items[0].item_id
    orchestrator, _ = build_orchestrator(
        clock,
        execution=ScriptedExecutionService(OutcomeStatus.SUCCESS),
        replan=ScriptedReplanService(ReplanDecision()),
        initial_state=initial_state,
    )

    outcomes = await orchestrator.run_ready()

    assert len(outcomes) == 1
    assert outcomes[0].status == OutcomeStatus.SUCCESS
    assert orchestrator.state.plan.active_day_item_id is not None
    assert orchestrator.state.plan.hour_plan_items == []
    assert orchestrator.state.plan.current_hour_summary == ""
    assert orchestrator.state.plan.minute_steps == []


@pytest.mark.anyio
async def test_hour_replan_rebuilds_the_hour_outline() -> None:
    clock = FakeClock(datetime(2026, 3, 23, 14, 0, tzinfo=UTC))
    initial_state = RuntimeState(
        plan=PlanState(
            plan_date="2026-03-23",
            day_summary="Keep the afternoon experiment stable.",
            current_hour_summary="Push the current lab thread forward.",
            hour_starts_at="2026-03-23T14:00:00+00:00",
            minute_steps=[
                PlanStep(
                    title="Inspect the latest reading",
                    detail="Look at the latest spectrometer output.",
                    scheduled_for="2026-03-23T14:00:00+00:00",
                ),
                PlanStep(
                    title="Continue the current lab thread",
                    detail="Stay on the original hour plan.",
                    scheduled_for="2026-03-23T14:05:00+00:00",
                ),
            ],
        )
    )
    original_step_ids = [step.step_id for step in initial_state.plan.minute_steps]
    orchestrator, _ = build_orchestrator(
        clock,
        execution=ScriptedExecutionService(OutcomeStatus.SUCCESS),
        replan=ScriptedReplanService(
            ReplanDecision(
                kind=ReplanKind.HOUR_REPLAN,
                reason="A stronger lab thread emerged from the last result.",
            )
        ),
        initial_state=initial_state,
    )

    outcomes = await orchestrator.run_ready()

    assert len(outcomes) == 1
    assert outcomes[0].status == OutcomeStatus.SUCCESS
    assert orchestrator.state.plan.day_blocks
    assert orchestrator.state.plan.day_blocks[0].time == "13:00-17:30"
    assert orchestrator.state.plan.minute_steps[0].scheduled_for == "2026-03-23T14:00:00+00:00"
    assert original_step_ids[1] not in [
        step.step_id for step in orchestrator.state.plan.minute_steps
    ]
