from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.core.events import EventSource, EventType, RuntimeEvent
from app.core.outcomes import OutcomeStatus
from app.core.state import EmotionState, PlanStep, RuntimeState
from app.core.types import ExecutionMode, ProviderName
from app.infra.model_client import ModelResponse, ModelRouter
from app.infra.settings import ModelRouteConfig, ModelRoutingSettings
from app.memory.service import MemoryNoteDraft
from app.persona.service import PersonaDraft, PersonaService
from app.runtime.execution import ExecutionService
from app.runtime.interaction import (
    InteractionAction,
    InteractionDirectiveDraft,
    InteractionPolicy,
)
from app.runtime.planning import PlanningService
from tests.test_support import (
    InMemoryJsonStore,
    build_in_memory_memory_service,
)


class FakeModelClient:
    def __init__(
        self,
        *,
        text_output: str = "",
        structured_outputs: dict[str, object] | None = None,
    ) -> None:
        self.text_output = text_output
        self.structured_outputs = structured_outputs or {}

    async def generate_text(self, request):
        return ModelResponse(
            provider=request.provider,
            model=request.model,
            text=self.text_output,
            raw={"role": request.role.value},
        )

    async def generate_structured(self, request, output_type):
        structured = self.structured_outputs[output_type.__name__]
        if not isinstance(structured, output_type):
            structured = output_type.model_validate(structured)
        return ModelResponse(
            provider=request.provider,
            model=request.model,
            text=str(structured),
            structured=structured,
            raw={"role": request.role.value},
        )


def build_routing_settings() -> ModelRoutingSettings:
    return ModelRoutingSettings(
        dialogue=ModelRouteConfig(
            provider=ProviderName.OPENAI,
            model="dialogue-test",
            api_key_env="OPENAI_API_KEY",
        ),
        decision=ModelRouteConfig(
            provider=ProviderName.OPENAI,
            model="decision-test",
            api_key_env="OPENAI_API_KEY",
        ),
        memory=ModelRouteConfig(
            provider=ProviderName.OPENAI,
            model="memory-test",
            api_key_env="OPENAI_API_KEY",
        ),
    )


@pytest.mark.anyio
async def test_persona_service_builds_structured_profile_from_model() -> None:
    router = ModelRouter(build_routing_settings())
    model = FakeModelClient(
        structured_outputs={
            "PersonaDraft": PersonaDraft(
                summary="Kurisu is a sharp researcher with a cool exterior and precise habits.",
                stable_traits=["sharp", "curious", "precise"],
                relationship_context="Kurisu trusts the user enough to speak candidly.",
                preferences=["quiet routines", "careful planning"],
            )
        }
    )

    service = PersonaService(
        profile_path=Path("memory/tests/persona.json"),
        model_client=model,
        model_router=router,
    )
    service.store = InMemoryJsonStore()
    profile = await service.bootstrap_from_text(
        "A sharp, curious researcher who likes quiet routines and careful planning.",
        name="Kurisu",
    )

    assert profile.summary.startswith("Kurisu is a sharp researcher")
    assert profile.stable_traits == ["sharp", "curious", "precise"]
    assert profile.preferences == ["quiet routines", "careful planning"]


@pytest.mark.anyio
async def test_interaction_policy_can_use_decision_model() -> None:
    router = ModelRouter(build_routing_settings())
    model = FakeModelClient(
        structured_outputs={
            "InteractionDirectiveDraft": InteractionDirectiveDraft(
                action=InteractionAction.RECORD_ONLY,
                reason="The message is only an acknowledgement.",
            )
        }
    )
    policy = InteractionPolicy(model_client=model, model_router=router)

    directive = await policy.evaluate(
        RuntimeEvent(
            event_type=EventType.MESSAGE_RECEIVED,
            source=EventSource.USER,
            payload={"text": "ok"},
        ),
        RuntimeState(),
    )

    assert directive.action == InteractionAction.RECORD_ONLY
    assert directive.reason == "The message is only an acknowledgement."


@pytest.mark.anyio
async def test_execution_service_generates_dialogue_reply_from_model() -> None:
    router = ModelRouter(build_routing_settings())
    model = FakeModelClient(text_output="我在，刚看到你的消息。")
    service = ExecutionService(model_client=model, model_router=router)

    outcome = await service.execute_step(
        PlanStep(
            title="Reply to the latest message",
            detail="Respond to the user first.",
            execution_mode=ExecutionMode.NARRATIVE,
            arguments={
                "audience": "user",
                "message_text": "你在做什么？",
                "message_excerpt": "你在做什么？",
            },
        ),
        RuntimeState(
            persona_summary="一个冷静、聪明、说话克制的研究者",
            emotion=EmotionState(summary="steady and quietly positive", valence=0.2),
        ),
    )

    assert outcome.status == OutcomeStatus.SUCCESS
    assert outcome.summary == "我在，刚看到你的消息。"


@pytest.mark.anyio
async def test_memory_and_planning_share_recalled_context() -> None:
    memory, _ = build_in_memory_memory_service()
    await memory.append_raw_event(
        RuntimeEvent(
            event_type=EventType.MESSAGE_RECEIVED,
            source=EventSource.USER,
            payload={"text": "我喜欢红茶，也喜欢安静一点的夜晚。"},
        )
    )

    planning = PlanningService(memory_service=memory)
    plan = await planning.plan_next_window(
        RuntimeState(persona_summary="偏好安静节奏的研究者"),
        RuntimeEvent(
            event_type=EventType.MESSAGE_RECEIVED,
            source=EventSource.USER,
            payload={"text": "你还记得我喜欢什么吗？"},
        ),
        now=datetime(2026, 3, 23, 14, 0, tzinfo=UTC),
    )

    assert "红茶" in plan.minute_steps[0].arguments["memory_context"]


@pytest.mark.anyio
async def test_memory_service_uses_memory_model_for_note_extraction() -> None:
    router = ModelRouter(build_routing_settings())
    model = FakeModelClient(
        structured_outputs={
            "MemoryNoteDraft": MemoryNoteDraft(
                content="User prefers red tea and quiet nights.",
                importance=0.82,
            )
        }
    )

    service, _ = build_in_memory_memory_service(
        model_client=model,
        model_router=router,
    )
    await service.append_raw_event(
        RuntimeEvent(
            event_type=EventType.MESSAGE_RECEIVED,
            source=EventSource.USER,
            payload={"text": "I prefer red tea and quiet nights."},
        )
    )

    assert service.active_entries[-1].content == "User prefers red tea and quiet nights."
    assert service.active_entries[-1].importance == 0.82
