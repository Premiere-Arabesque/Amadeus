from fastapi.testclient import TestClient

from app.infra.model_client import (
    ModelClient,
    ModelRequest,
    StructuredResponse,
    TextResponse,
)
from app.infra.settings import ModelRoute, ModelRoutingSettings
from app.main import create_app
from tests.test_support import (
    build_in_memory_memory_service,
    build_in_memory_persona_service,
)


class FakeModelClient(ModelClient):
    async def generate_text(self, request: ModelRequest) -> TextResponse:
        return TextResponse(text=request.prompt)

    async def generate_structured(self, request: ModelRequest, schema_type):
        if schema_type.__name__ == "DayPlanDraft":
            return StructuredResponse(
                structured=schema_type.model_validate(
                    [
                        {
                            "time": "00:00-23:59",
                            "label": "Model planned the next autonomous window.",
                        }
                    ]
                )
            )
        if schema_type.__name__ == "MinuteActionPlanDraft":
            return StructuredResponse(
                structured=schema_type.model_validate(
                    [
                        {
                            "action_description": (
                                "Quietly scan recent notes and decide the next small action."
                            ),
                            "duration_minutes": 5,
                        },
                        {
                            "action_description": "Stay with the thread that is already in motion.",
                            "duration_minutes": 5,
                        },
                    ]
                )
            )
        if schema_type.__name__ == "NarrativeExecutionDraft":
            return StructuredResponse(
                structured=schema_type.model_validate(
                    {
                        "scene": "The persona quietly reviewed the latest notes and context.",
                        "result": "Model-simulated autonomous action completed cleanly.",
                    }
                )
            )
        if schema_type.__name__ == "MemorySummaryDraft":
            return StructuredResponse(
                structured=schema_type.model_validate(
                    {"content": "Model distilled a durable autonomous memory."}
                )
            )
        return StructuredResponse(structured=schema_type.model_validate({}))


def configured_routes() -> ModelRoutingSettings:
    return ModelRoutingSettings(
        dialogue=ModelRoute(
            provider="custom",
            model="dialogue-x",
            base_url="https://mock/dialogue",
        ),
        decision=ModelRoute(
            provider="custom",
            model="decision-x",
            base_url="https://mock/decision",
        ),
        memory=ModelRoute(provider="custom", model="memory-x", base_url="https://mock/memory"),
    )


def test_runtime_run_once_boots_autonomous_loop_with_model_planning() -> None:
    memory_service, _ = build_in_memory_memory_service()
    persona_service, _ = build_in_memory_persona_service()
    app = create_app(
        memory_service=memory_service,
        persona_service=persona_service,
        routing_settings=configured_routes(),
        model_client=FakeModelClient(),
    )
    client = TestClient(app)

    response = client.post("/api/runtime/run-once")
    memory_response = client.get("/api/memory", params={"limit": 5})

    assert response.status_code == 200
    assert memory_response.status_code == 200
    payload = response.json()
    memory_payload = memory_response.json()
    assert payload["outcome"]["content"] == "Model-simulated autonomous action completed cleanly."
    assert payload["outbound_messages"] == []
    assert payload["state"]["plan_summary"] == "Model planned the next autonomous window."
    assert payload["state"]["current_action_id"].startswith("step_")
    assert payload["state"]["next_step_scheduled_for"]
    assert (
        memory_payload["active_entries"][0]["content"]
        == "Model distilled a durable autonomous memory."
    )
