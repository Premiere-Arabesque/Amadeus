from datetime import UTC, datetime

import pytest

from app.communication.hub import CommunicationHub
from app.core.outcomes import ActionOutcome, OutcomeStatus, ReplanDecision, ReplanKind
from app.core.state import DayPlanBlock, PlanOutlineStatus, PlanState, RuntimeState
from app.core.types import ExecutionGranularity, ExecutionMode, ExecutionZone
from app.runtime.orchestrator import OrchestratorServices, RuntimeOrchestrator
from app.runtime.planning import PlanningService


class RecordingExecutionService:
    def __init__(self) -> None:
        self.steps = []

    async def execute_step(self, step, **kwargs):
        del kwargs
        self.steps.append(step)
        return ActionOutcome(
            action_id=step.step_id,
            status=OutcomeStatus.SUCCESS,
            mode=ExecutionMode.NARRATIVE,
            source=ExecutionZone.NON_REAL,
            content=f"executed {step.title}",
        )


class StaticReplanService:
    async def decide(self, **kwargs) -> ReplanDecision:
        del kwargs
        return ReplanDecision(kind=ReplanKind.NO_REPLAN, reason="")


class RecordingMemoryService:
    def __init__(self) -> None:
        self.saved_states = []
        self.recorded_outcomes = []

    def record_runtime_event(self, event) -> None:
        del event

    async def save_snapshot(self, state) -> None:
        self.saved_states.append(state.model_copy(deep=True))

    def update_plan_context(self, *, day_blocks, plan_date) -> None:
        del day_blocks, plan_date

    async def summarize_outcome(self, step, outcome, *, state, event) -> str:
        del step, state, event
        return outcome.content

    def record_outcome(self, step, outcome, *, memory_content, interaction_partner=None) -> None:
        del interaction_partner
        self.recorded_outcomes.append((step, outcome, memory_content))

    def record_replan_decision(self, *args, **kwargs) -> None:
        del args, kwargs


@pytest.mark.anyio
async def test_hour_granularity_expand_ready_block_keeps_day_blocks_without_minute_steps() -> None:
    service = PlanningService(execution_granularity=ExecutionGranularity.HOUR)
    active_block = DayPlanBlock(
        time="19:00-21:00",
        label="晚间休息放松",
        status=PlanOutlineStatus.ACTIVE,
    )
    state = RuntimeState(
        plan=PlanState(
            plan_date="2026-04-03",
            day_blocks=[active_block],
            active_block_id=active_block.block_id,
        )
    )

    expanded = await service.expand_ready_block(
        state,
        now=datetime(2026, 4, 3, 19, 30, tzinfo=UTC),
    )

    assert expanded is not None
    assert expanded.active_block_id == active_block.block_id
    assert expanded.minute_steps == []


@pytest.mark.anyio
async def test_orchestrator_executes_active_day_block_in_hour_granularity() -> None:
    first_block = DayPlanBlock(
        time="19:00-21:00",
        label="晚间休息放松",
        status=PlanOutlineStatus.ACTIVE,
    )
    second_block = DayPlanBlock(
        time="21:00-22:00",
        label="洗漱准备睡觉",
        status=PlanOutlineStatus.PENDING,
    )
    planning = PlanningService(execution_granularity=ExecutionGranularity.HOUR)
    execution = RecordingExecutionService()
    memory = RecordingMemoryService()
    orchestrator = RuntimeOrchestrator(
        services=OrchestratorServices(
            planning=planning,
            execution=execution,
            replan=StaticReplanService(),
            interaction=object(),
            memory=memory,
            communication=CommunicationHub(),
        ),
        initial_state=RuntimeState(
            plan=PlanState(
                plan_date="2026-04-03",
                day_blocks=[first_block, second_block],
                active_block_id=first_block.block_id,
            )
        ),
        now_provider=lambda: datetime(2026, 4, 3, 19, 30, tzinfo=UTC),
    )

    outcome = await orchestrator.run_once()

    assert outcome is not None
    assert outcome.content == "executed 晚间休息放松"
    assert len(execution.steps) == 1
    assert execution.steps[0].title == "晚间休息放松"
    assert execution.steps[0].detail == ""
    assert orchestrator.state.plan.day_blocks[0].status == PlanOutlineStatus.COMPLETE
    assert orchestrator.state.plan.active_block_id == second_block.block_id


@pytest.mark.anyio
async def test_sync_plan_to_time_updates_active_block_and_clears_stale_minute_steps() -> None:
    service = PlanningService(execution_granularity=ExecutionGranularity.MINUTE)
    first_block = DayPlanBlock(
        time="08:00-08:30",
        label="出门前检查书包",
        status=PlanOutlineStatus.ACTIVE,
    )
    second_block = DayPlanBlock(
        time="16:30-17:30",
        label="放学后整理东西",
        status=PlanOutlineStatus.PENDING,
    )
    state = RuntimeState(
        plan=PlanState(
            plan_date="2026-04-04",
            day_blocks=[first_block, second_block],
            active_block_id=first_block.block_id,
            minute_steps=[
                {
                    "title": "检查书包",
                    "detail": "",
                    "scheduled_for": "2026-04-04T08:00:00+00:00",
                }
            ],
        )
    )

    synced = await service.sync_plan_to_time(
        state,
        now=datetime(2026, 4, 4, 16, 48, tzinfo=UTC),
    )

    assert synced.active_block_id == second_block.block_id
    assert synced.minute_steps == []
