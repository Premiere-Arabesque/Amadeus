from datetime import UTC, datetime, timedelta

import pytest

from app.core.events import EventSource, EventType, RuntimeEvent
from app.core.outcomes import ActionOutcome, OutcomeStatus
from app.core.state import PlanStep, RuntimeState
from app.core.types import ExecutionZone
from app.infra.model_client import (
    ModelClient,
    ModelRequest,
    ModelRouter,
    StructuredResponse,
    TextResponse,
)
from app.infra.settings import ModelRole, ModelRoute, ModelRoutingSettings
from app.mcp.registry import CapabilityRegistry
from app.mcp.schemas import ActionResult, CapabilityDescriptor
from app.memory.models import ActiveMemoryEntry
from app.runtime.execution import ExecutionLoopContext, ExecutionService
from tests.test_support import (
    InMemoryJsonlStore,
    MemoryHarness,
    build_in_memory_memory_service,
)


class FakeMCPLayer:
    def __init__(
        self,
        result: ActionResult,
        *,
        registered_capabilities: dict[str, list[str]] | None = None,
        results_by_capability: dict[str, ActionResult] | None = None,
    ) -> None:
        self.result = result
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.results_by_capability = results_by_capability or {}
        self.registry = CapabilityRegistry()
        for capability, required_arguments in (registered_capabilities or {}).items():
            self.registry.register(
                CapabilityDescriptor(
                    name=capability,
                    description=f"Fake capability for {capability}.",
                    required_arguments=required_arguments,
                ),
                self._execute_registered,
            )

    async def call(self, capability: str, arguments: dict[str, object]) -> ActionResult:
        self.calls.append((capability, arguments))
        return self.results_by_capability.get(capability, self.result)

    async def invoke(self, capability: str, arguments: dict[str, object]) -> ActionResult:
        return await self.call(capability, arguments)

    def get_descriptor(self, capability: str):
        return self.registry.get_descriptor(capability)

    async def _execute_registered(self, arguments: dict[str, object]) -> ActionResult:
        return self.result


class RecordingModelClient(ModelClient):
    def __init__(self) -> None:
        self.roles: list[ModelRole] = []

    async def generate_text(self, request: ModelRequest):
        raise NotImplementedError

    async def generate_structured(self, request: ModelRequest, schema_type):
        self.roles.append(request.role)
        return StructuredResponse(
            structured=schema_type.model_validate(
                {
                    "scene": "Picked up the unresolved experiment thread from prior work.",
                    "detail_elaboration": (
                        "Simulated one concrete intermediate experiment pass and kept the "
                        "result continuity-preserving."
                    ),
                    "result": "Generated a plausible intermediate experiment result.",
                }
            )
        )


class SchemaDispatchingModelClient(ModelClient):
    def __init__(self, payloads: dict[str, dict[str, str]]) -> None:
        self.payloads = payloads
        self.roles: list[ModelRole] = []

    async def generate_text(self, request: ModelRequest):
        raise NotImplementedError

    async def generate_structured(self, request: ModelRequest, schema_type):
        self.roles.append(request.role)
        return StructuredResponse(
            structured=schema_type.model_validate(self.payloads[schema_type.__name__])
        )


class LoopingDialogueModelClient(ModelClient):
    def __init__(self, agent_reaction: str) -> None:
        self.agent_reaction = agent_reaction
        self.roles: list[ModelRole] = []

    async def generate_text(self, request: ModelRequest) -> TextResponse:
        self.roles.append(request.role)
        return TextResponse(text=self.agent_reaction)

    async def generate_structured(self, request: ModelRequest, schema_type):
        self.roles.append(request.role)
        return StructuredResponse(
            structured=schema_type.model_validate(
                {
                    "scene": "The executor turned the latest observation into a natural scene.",
                    "result": (
                        "The executor advanced the current thread with one more concrete "
                        "beat."
                    ),
                }
            )
        )


def build_decision_router() -> ModelRouter:
    return ModelRouter(
        ModelRoutingSettings(
            decision=ModelRoute(
                provider="custom",
                model="decision-x",
                base_url="https://mock/decision",
            )
        )
    )


@pytest.mark.anyio
async def test_narrative_execution_produces_staged_trace_without_model() -> None:
    service = ExecutionService(
        FakeMCPLayer(ActionResult(status=OutcomeStatus.SUCCESS, summary="ok")),
        max_inner_loop_turns=1,
    )

    outcome = await service.execute_step(
        PlanStep(
            title="Walk to the cafeteria",
            detail="Head downstairs and pick a simple lunch.",
            zone_hint=ExecutionZone.WEAK_REAL,
        ),
        state=RuntimeState(persona_summary="A graduate student keeping the day ordinary."),
    )

    assert outcome.status == OutcomeStatus.SUCCESS
    assert outcome.execution_trace[0].stage == "scene"
    assert outcome.execution_trace[1].stage == "result"
    assert outcome.execution_trace[0].content
    assert outcome.content == outcome.execution_trace[1].content
    assert outcome.raw_data["scene"] == outcome.execution_trace[0].content


@pytest.mark.anyio
async def test_tool_execution_preserves_tool_result_and_staged_trace() -> None:
    mcp_layer = FakeMCPLayer(
        ActionResult(
            status=OutcomeStatus.SUCCESS,
            summary="Example Paper: Quantum bananas improve time travel stability.",
            raw={
                "title": "Example Paper",
                "content": "Quantum bananas improve time travel stability.",
            },
        )
    )
    service = ExecutionService(mcp_layer, max_inner_loop_turns=1)

    outcome = await service.execute_step(
        PlanStep(
            title="Read the shared page",
            detail="Open the paper and inspect the main claim.",
            zone_hint=ExecutionZone.REAL,
            capability="read_url",
            arguments={"url": "https://example.com/paper"},
        ),
        state=RuntimeState(persona_summary="A careful reader."),
    )

    assert mcp_layer.calls == [("read_url", {"url": "https://example.com/paper"})]
    assert outcome.status == OutcomeStatus.SUCCESS
    assert outcome.tool_invocations[0].capability == "read_url"
    assert outcome.execution_trace[0].stage == "tool_scene"
    assert outcome.execution_trace[1].stage == "tool_result"
    assert "Example Paper" in outcome.content
    assert outcome.raw_data["result"]["content"] == "Quantum bananas improve time travel stability."


@pytest.mark.anyio
async def test_execution_infers_read_url_capability_from_step_text() -> None:
    mcp_layer = FakeMCPLayer(
        ActionResult(
            status=OutcomeStatus.SUCCESS,
            summary="Read Example Paper: grounded summary.",
            raw={"title": "Example Paper", "content": "grounded summary"},
        ),
        registered_capabilities={"read_url": ["url"]},
    )
    service = ExecutionService(mcp_layer, max_inner_loop_turns=1)

    outcome = await service.execute_step(
        PlanStep(
            title="Inspect the shared page",
            detail="Open https://example.com/paper and capture the key claim.",
            zone_hint=ExecutionZone.WEAK_REAL,
        ),
        state=RuntimeState(persona_summary="A careful reader."),
    )

    assert mcp_layer.calls == [("read_url", {"url": "https://example.com/paper"})]
    assert outcome.source == ExecutionZone.REAL
    assert outcome.tool_invocations[0].capability == "read_url"
    assert outcome.execution_trace[0].capability == "read_url"
    assert outcome.execution_trace[1].capability == "read_url"


@pytest.mark.anyio
async def test_execution_infers_search_capability_from_step_text() -> None:
    mcp_layer = FakeMCPLayer(
        ActionResult(
            status=OutcomeStatus.SUCCESS,
            summary="Searched web for agent memory: found three leads.",
            raw={"query": "agent memory architectures", "results": []},
        ),
        registered_capabilities={"search_web": ["query"]},
    )
    service = ExecutionService(mcp_layer, max_inner_loop_turns=1)

    outcome = await service.execute_step(
        PlanStep(
            title="Search for background context",
            detail="Search: agent memory architectures",
            zone_hint=ExecutionZone.WEAK_REAL,
        ),
        state=RuntimeState(persona_summary="A careful researcher."),
    )

    assert mcp_layer.calls == [("search_web", {"query": "agent memory architectures"})]
    assert outcome.source == ExecutionZone.REAL
    assert outcome.tool_invocations[0].capability == "search_web"


@pytest.mark.anyio
async def test_tool_failure_falls_back_with_trace() -> None:
    service = ExecutionService(
        FakeMCPLayer(
            ActionResult(
                status=OutcomeStatus.BLOCKED_FAILURE,
                summary="The upstream site rejected the request.",
                raw={"detail": "403 forbidden"},
            )
        ),
        max_inner_loop_turns=1,
    )

    outcome = await service.execute_step(
        PlanStep(
            title="Read the shared page",
            detail="Open the page and keep the thread moving.",
            zone_hint=ExecutionZone.REAL,
            capability="read_url",
            arguments={"url": "https://example.com/paper"},
        ),
        state=RuntimeState(persona_summary="A careful reader."),
        event=RuntimeEvent(
            event_type=EventType.MESSAGE_RECEIVED,
            source=EventSource.USER,
            payload={"text": "Please check the page."},
        ),
    )

    assert outcome.status == OutcomeStatus.PARTIAL_SUCCESS
    assert outcome.source == ExecutionZone.WEAK_REAL
    assert outcome.execution_trace[0].stage == "fallback_scene"
    assert outcome.execution_trace[1].stage == "fallback_result"
    assert outcome.raw_data["fallback"] is True


@pytest.mark.anyio
async def test_weak_real_multi_turn_steps_record_agent_executor_loop() -> None:
    service = ExecutionService(
        FakeMCPLayer(ActionResult(status=OutcomeStatus.SUCCESS, summary="ok")),
        max_inner_loop_turns=2,
    )

    outcome = await service.execute_step(
        PlanStep(
            title="Walk to the cafeteria",
            detail="Head downstairs and pick a simple lunch.",
            minutes=10,
            zone_hint=ExecutionZone.WEAK_REAL,
        ),
        state=RuntimeState(persona_summary="A graduate student keeping the day ordinary."),
    )

    assert [entry.stage for entry in outcome.execution_trace] == [
        "scene",
        "result",
        "agent_response_1",
        "loop_scene_1",
        "loop_result_1",
        "agent_response_2",
        "loop_stop",
    ]
    assert len(outcome.raw_data["agent_responses"]) == 2
    assert outcome.raw_data["loop_turn_budget"] == 2
    assert outcome.raw_data["loop_stop_reason"] == "max_rounds"
    assert "loop_final_result" not in outcome.raw_data
    assert outcome.content == "Walk to the cafeteria: Head downstairs and pick a simple lunch."


@pytest.mark.anyio
async def test_real_zone_loop_can_trigger_a_follow_up_tool_from_agent_response() -> None:
    model_client = LoopingDialogueModelClient("Search: agent memory architectures")
    service = ExecutionService(
        FakeMCPLayer(
            ActionResult(status=OutcomeStatus.SUCCESS, summary="ok"),
            registered_capabilities={"search_web": ["query"]},
            results_by_capability={
                "read_url": ActionResult(
                    status=OutcomeStatus.SUCCESS,
                    summary="Read Example Paper: a grounded summary.",
                    raw={"title": "Example Paper", "content": "a grounded summary"},
                ),
                "search_web": ActionResult(
                    status=OutcomeStatus.SUCCESS,
                    summary="Searched web for agent memory architectures: found three leads.",
                    raw={"query": "agent memory architectures", "results": []},
                ),
            },
        ),
        model_client=model_client,
        model_router=ModelRouter(
            ModelRoutingSettings(
                dialogue=ModelRoute(
                    provider="custom",
                    model="dialogue-x",
                    base_url="https://mock/dialogue",
                )
            )
        ),
        max_inner_loop_turns=2,
    )

    outcome = await service.execute_step(
        PlanStep(
            title="Inspect the shared page",
            detail="Open https://example.com/paper and capture the key claim.",
            minutes=10,
            zone_hint=ExecutionZone.REAL,
            capability="read_url",
            arguments={"url": "https://example.com/paper"},
        ),
        state=RuntimeState(persona_summary="A careful researcher."),
    )

    assert [invocation.capability for invocation in outcome.tool_invocations] == [
        "read_url",
        "search_web",
    ]
    assert outcome.tool_invocations[1].arguments == {"query": "agent memory architectures"}
    assert "agent_response_1" in [entry.stage for entry in outcome.execution_trace]
    assert "loop_tool_result_1" in [entry.stage for entry in outcome.execution_trace]
    assert outcome.raw_data["loop_tool_results"][0]["query"] == "agent memory architectures"


@pytest.mark.anyio
def test_execution_loop_budget_uses_configured_max_rounds() -> None:
    service = ExecutionService(
        FakeMCPLayer(ActionResult(status=OutcomeStatus.SUCCESS, summary="ok")),
        max_inner_loop_turns=7,
    )

    assert (
        service._loop_turn_budget(
            PlanStep(title="Short step", detail="Five minutes.", minutes=5)
        )
        == 7
    )
    assert service._loop_turn_budget(
        PlanStep(title="Long step", detail="Thirty minutes.", minutes=30)
    ) == 7


class AdvancingTimeProbe:
    def __init__(self, *values: datetime) -> None:
        self.values = list(values)
        self.index = 0

    def __call__(self) -> datetime:
        if not self.values:
            raise RuntimeError("No probe values configured.")
        value = self.values[min(self.index, len(self.values) - 1)]
        self.index += 1
        return value


class ToggleInterruptProbe:
    def __init__(self, triggers_on_call: int) -> None:
        self.triggers_on_call = triggers_on_call
        self.calls = 0

    def __call__(self) -> bool:
        self.calls += 1
        return self.calls >= self.triggers_on_call


@pytest.mark.anyio
async def test_execution_loop_stops_when_pre_replan_buffer_is_exhausted() -> None:
    start = datetime(2026, 3, 26, 12, 0, 0, tzinfo=UTC)
    service = ExecutionService(
        FakeMCPLayer(ActionResult(status=OutcomeStatus.SUCCESS, summary="ok")),
        max_inner_loop_turns=3,
        loop_pre_replan_buffer_seconds=30,
    )

    outcome = await service.execute_step(
        PlanStep(
            title="Walk to the cafeteria",
            detail="Head downstairs and pick a simple lunch.",
            minutes=10,
            zone_hint=ExecutionZone.WEAK_REAL,
        ),
        state=RuntimeState(persona_summary="A graduate student keeping the day ordinary."),
        loop_context=ExecutionLoopContext(
            now_provider=AdvancingTimeProbe(
                start,
                start + timedelta(seconds=15),
            ),
            next_step_scheduled_for=start + timedelta(seconds=40),
        ),
    )

    assert [entry.stage for entry in outcome.execution_trace] == [
        "scene",
        "result",
        "agent_response_1",
        "loop_stop",
    ]
    assert outcome.raw_data["loop_stop_reason"] == "buffer_exhausted"
    assert outcome.content == "Walk to the cafeteria: Head downstairs and pick a simple lunch."


@pytest.mark.anyio
async def test_execution_loop_stops_between_turns_when_external_interrupt_arrives() -> None:
    service = ExecutionService(
        FakeMCPLayer(ActionResult(status=OutcomeStatus.SUCCESS, summary="ok")),
        max_inner_loop_turns=3,
    )

    outcome = await service.execute_step(
        PlanStep(
            title="Walk to the cafeteria",
            detail="Head downstairs and pick a simple lunch.",
            minutes=10,
            zone_hint=ExecutionZone.WEAK_REAL,
        ),
        state=RuntimeState(persona_summary="A graduate student keeping the day ordinary."),
        loop_context=ExecutionLoopContext(
            should_interrupt=ToggleInterruptProbe(triggers_on_call=2),
        ),
    )

    assert [entry.stage for entry in outcome.execution_trace] == [
        "scene",
        "result",
        "agent_response_1",
        "loop_stop",
    ]
    assert outcome.raw_data["loop_stop_reason"] == "external_interrupt"
    assert outcome.content == "Walk to the cafeteria: Head downstairs and pick a simple lunch."


@pytest.mark.anyio
async def test_no_tool_step_can_route_to_ambiguity_via_decision_model() -> None:
    model_client = SchemaDispatchingModelClient(
        {
            "NarrativeZoneDecision": {"zone": "Ambiguity Zone"},
            "AmbiguityExecutionDraft": {
                "scene": "Picked up the unresolved experiment thread from prior work.",
                "detail_elaboration": (
                    "Simulated one concrete intermediate experiment pass and preserved "
                    "continuity."
                ),
                "result": "Generated a plausible intermediate experiment result.",
            },
        }
    )
    service = ExecutionService(
        FakeMCPLayer(ActionResult(status=OutcomeStatus.SUCCESS, summary="ok")),
        model_client=model_client,
        model_router=build_decision_router(),
        max_inner_loop_turns=1,
    )

    outcome = await service.execute_step(
        PlanStep(
            title="Continue the experiment",
            detail="Inspect the loss trend and run the next training pass.",
            zone_hint=ExecutionZone.WEAK_REAL,
        ),
        state=RuntimeState(persona_summary="A machine learning researcher."),
    )

    assert model_client.roles == [ModelRole.DECISION, ModelRole.DECISION]
    assert outcome.source == ExecutionZone.AMBIGUITY
    assert outcome.raw_data["detail_elaboration"].startswith("Simulated one concrete")


@pytest.mark.anyio
async def test_tool_failure_can_fall_back_to_ambiguity_when_decision_model_picks_it() -> None:
    model_client = SchemaDispatchingModelClient(
        {
            "NarrativeZoneDecision": {"zone": "Ambiguity Zone"},
            "AmbiguityExecutionDraft": {
                "scene": "Picked up the unresolved experiment thread from prior work.",
                "detail_elaboration": (
                    "Simulated one concrete intermediate experiment pass and preserved "
                    "continuity."
                ),
                "result": "Generated a plausible intermediate experiment result.",
            },
        }
    )
    mcp_layer = FakeMCPLayer(
        ActionResult(
            status=OutcomeStatus.BLOCKED_FAILURE,
            summary="The page could not be fetched.",
            raw={"detail": "403 forbidden"},
        ),
        registered_capabilities={"read_url": ["url"]},
    )
    service = ExecutionService(
        mcp_layer,
        model_client=model_client,
        model_router=build_decision_router(),
        max_inner_loop_turns=1,
    )

    outcome = await service.execute_step(
        PlanStep(
            title="Continue the experiment",
            detail="Read https://example.com/log and keep the training thread moving.",
            zone_hint=ExecutionZone.REAL,
        ),
        state=RuntimeState(persona_summary="A machine learning researcher."),
    )

    assert mcp_layer.calls == [("read_url", {"url": "https://example.com/log"})]
    assert model_client.roles == [ModelRole.DECISION, ModelRole.DECISION]
    assert outcome.status == OutcomeStatus.PARTIAL_SUCCESS
    assert outcome.source == ExecutionZone.AMBIGUITY
    assert outcome.execution_trace[1].stage == "fallback_detail_elaboration"
    assert outcome.raw_data["fallback_zone"] == ExecutionZone.AMBIGUITY.value


@pytest.mark.anyio
async def test_ambiguity_execution_uses_continuity_context_and_detail_elaboration() -> None:
    memory_service, _ = build_in_memory_memory_service(
        harness=MemoryHarness(
            active_store=InMemoryJsonlStore(
                [
                    ActiveMemoryEntry(
                        created_at="2026-03-23T10:00:00+00:00",
                        content=(
                            "Yesterday the experiment loss was 0.8 after gradient clipping."
                        ),
                        source="Ambiguity Zone",
                    ).model_dump(mode="json")
                ]
            )
        )
    )
    service = ExecutionService(
        FakeMCPLayer(ActionResult(status=OutcomeStatus.SUCCESS, summary="ok")),
        memory_service=memory_service,
        max_inner_loop_turns=1,
    )

    outcome = await service.execute_step(
        PlanStep(
            title="Continue the experiment",
            detail="Inspect the loss trend and run the next training pass.",
            zone_hint=ExecutionZone.AMBIGUITY,
        ),
        state=RuntimeState(persona_summary="A machine learning researcher."),
    )

    assert outcome.status == OutcomeStatus.SUCCESS
    assert outcome.source == ExecutionZone.AMBIGUITY
    assert outcome.execution_trace[0].stage == "continuity_context"
    assert "loss was 0.8" in outcome.execution_trace[0].content
    assert outcome.execution_trace[1].stage == "ambiguity_scene"
    assert outcome.execution_trace[2].stage == "detail_elaboration"
    assert outcome.raw_data["continuity_context"]
    assert outcome.raw_data["detail_elaboration"]


@pytest.mark.anyio
async def test_ambiguity_execution_uses_decision_route_when_model_is_available() -> None:
    model_client = RecordingModelClient()
    model_router = build_decision_router()
    service = ExecutionService(
        FakeMCPLayer(ActionResult(status=OutcomeStatus.SUCCESS, summary="ok")),
        model_client=model_client,
        model_router=model_router,
        max_inner_loop_turns=1,
    )

    outcome = await service.execute_step(
        PlanStep(
            title="Continue the experiment",
            detail="Inspect the loss trend and run the next training pass.",
            zone_hint=ExecutionZone.AMBIGUITY,
        ),
        state=RuntimeState(persona_summary="A machine learning researcher."),
    )

    assert model_client.roles == [ModelRole.DECISION]
    assert outcome.source == ExecutionZone.AMBIGUITY
    assert outcome.raw_data["detail_elaboration"].startswith("Simulated one concrete")


def test_action_outcome_accepts_legacy_input_names_but_serializes_canonical_shape() -> None:
    outcome = ActionOutcome.model_validate(
        {
            "action_id": "step_legacy",
            "status": "success",
            "mode": "narrative",
            "zone": ExecutionZone.WEAK_REAL,
            "summary": "Stayed with the current thread.",
            "raw": {"detail": "legacy payload"},
        }
    )

    dumped = outcome.model_dump(mode="json")

    assert outcome.source == ExecutionZone.WEAK_REAL
    assert outcome.content == "Stayed with the current thread."
    assert outcome.raw_data == {"detail": "legacy payload"}
    assert outcome.zone == outcome.source
    assert outcome.summary == outcome.content
    assert outcome.raw == outcome.raw_data
    assert dumped["source"] == "Weak Real Zone"
    assert dumped["content"] == "Stayed with the current thread."
    assert dumped["raw_data"] == {"detail": "legacy payload"}
    assert "zone" not in dumped
    assert "summary" not in dumped
    assert "raw" not in dumped
