from pathlib import Path

from fastapi.testclient import TestClient

import app.main as main_module
from app.core.outcomes import ActionOutcome, OutcomeStatus, ToolInvocation
from app.core.state import PlanStep
from app.core.types import ExecutionMode, ExecutionZone
from app.infra.model_client import ModelClient, ModelRequest, ModelTracePayload, TextResponse
from app.infra.settings import ModelRole, ModelRoutingSettings
from app.main import create_app
from app.memory.models import ActiveMemoryEntry
from app.persona.registry import PersonaRegistry
from app.planlab_main import create_app as create_planner_lab_app
from tests.test_support import (
    InMemoryJsonlStore,
    MemoryHarness,
    PersonaHarness,
    build_in_memory_memory_service,
    build_in_memory_persona_service,
)


class StubMCPProvider:
    def __init__(self) -> None:
        self.registered = False

    async def register_tools(self, registry) -> None:
        del registry
        self.registered = True

    async def close(self) -> None:
        return None

    def configured_server_count(self) -> int:
        return 1

    def connected_server_count(self) -> int:
        return 1

    def registered_tool_count(self) -> int:
        return 2

    def server_status(self):
        return [
            {
                "server_id": "stub-server",
                "transport": "stdio",
                "connected": True,
                "registered_tools": ["alpha", "beta"],
                "tool_count": 2,
            }
        ]


class RecordingTextModelClient(ModelClient):
    def __init__(self) -> None:
        self.requests: list[ModelRequest] = []

    async def generate_text(self, request: ModelRequest) -> TextResponse:
        self.requests.append(request)
        return TextResponse(text="OK", provider_name="stub-provider")

    async def generate_structured(self, request: ModelRequest, schema_type):
        del request, schema_type
        raise NotImplementedError


def test_post_message_runs_single_cycle() -> None:
    memory_service, _ = build_in_memory_memory_service()
    persona_service, _ = build_in_memory_persona_service()
    app = create_app(
        memory_service=memory_service,
        persona_service=persona_service,
        routing_settings=ModelRoutingSettings(),
    )
    client = TestClient(app)

    response = client.post(
        "/api/messages",
        json={
            "user_id": "user-1",
            "channel": "api",
            "text": "hello amadeus",
        },
    )

    assert response.status_code == 404
    payload = response.json()
    assert payload["event_id"].startswith("evt_")
    assert payload["outcome"]["status"] == "success"
    assert payload["outbound_messages"]
    assert payload["state"]["runtime_status"] in {"idle", "waiting"}
    assert payload["state"]["pending_event_count"] == 0
    assert payload["state"]["last_progress_at"]
    assert payload["state"]["last_outcome_status"] == "success"
    assert payload["state"]["last_error"] is None
    assert payload["state"]["clock_mode"] == "manual"
    assert payload["state"]["next_wake_at"]
    assert payload["state"]["next_step_scheduled_for"]
    assert payload["state"]["mcp_configured_server_count"] == 0
    assert payload["state"]["mcp_connected_server_count"] == 0
    assert payload["state"]["mcp_registered_tool_count"] == 0


def test_runtime_and_memory_inspection_endpoints() -> None:
    memory_service, _ = build_in_memory_memory_service()
    persona_service, _ = build_in_memory_persona_service()
    app = create_app(
        memory_service=memory_service,
        persona_service=persona_service,
    )
    client = TestClient(app)

    client.post(
        "/api/messages",
        json={
            "user_id": "user-1",
            "channel": "api",
            "text": "hello amadeus",
        },
    )

    state_response = client.get("/api/runtime/state")
    memory_response = client.get("/api/memory", params={"limit": 5})

    assert state_response.status_code == 200
    assert memory_response.status_code == 200

    state_payload = state_response.json()
    memory_payload = memory_response.json()

    assert state_payload["state"]["current_action_id"].startswith("step_")
    assert state_payload["latest_snapshot_id"].startswith("snap_")
    assert state_payload["next_wake_at"]
    assert state_payload["next_step_id"].startswith("step_")
    assert state_payload["summary"]["runtime_status"] in {"idle", "waiting"}
    assert state_payload["summary"]["pending_event_count"] == 0
    assert state_payload["summary"]["last_progress_at"]
    assert state_payload["summary"]["last_outcome_status"] == "success"
    assert state_payload["summary"]["last_error"] is None
    assert state_payload["summary"]["mcp_configured_server_count"] == 0
    assert state_payload["summary"]["mcp_connected_server_count"] == 0
    assert state_payload["summary"]["mcp_registered_tool_count"] == 0
    assert memory_payload["active_entries"]
    assert memory_payload["raw_entries"]


def test_reset_core_memory_endpoint_keeps_only_soul_derived_fields() -> None:
    memory_service, _ = build_in_memory_memory_service()
    persona_service, _ = build_in_memory_persona_service()
    persona_service.replace_soul_markdown(
        "# Soul: Kurisu\n\n"
        "## Core\n"
        "A careful researcher.\n\n"
        "Trusted collaborator of the user."
    )
    app = create_app(
        memory_service=memory_service,
        persona_service=persona_service,
    )
    client = TestClient(app)

    memory_service.core_memory.today_plan_summary = "Finish today's thread."
    memory_service.core_memory.recent_events = ["event-a", "event-b"]
    memory_service.core_memory.today_execution_records = []
    memory_service.core_store.write(memory_service.core_memory.model_dump(mode="json"))

    response = client.post("/api/memory/core/reset", params={"limit": 5})

    assert response.status_code == 200
    payload = response.json()
    assert payload["core_memory"]["today_plan_summary"] == ""
    assert payload["core_memory"]["recent_events"] == []
    assert payload["core_memory"]["today_execution_records"] == []

def test_runtime_debug_endpoint_surfaces_execution_replan_and_tools() -> None:
    memory_service, _ = build_in_memory_memory_service()
    persona_service, _ = build_in_memory_persona_service()
    app = create_app(
        memory_service=memory_service,
        persona_service=persona_service,
    )
    client = TestClient(app)

    client.post(
        "/api/messages",
        json={
            "user_id": "user-debug",
            "channel": "api",
            "text": "please keep going",
        },
    )

    response = client.get("/api/runtime/debug")

    assert response.status_code == 200
    payload = response.json()

    assert payload["summary"]["runtime_status"] in {"idle", "waiting"}
    assert payload["controls"]["supports_run_once"] is True
    assert payload["controls"]["supports_pause_resume"] is True
    assert payload["controls"]["supports_clock_control"] is True
    assert payload["current_plan"]["minute_steps"]
    assert payload["latest_execution"]["step"]["step_id"].startswith("step_")
    assert payload["latest_execution"]["outcome"]["status"] == "success"
    assert payload["latest_replan"]["decision"]["kind"] in {
        "no_replan",
        "micro_replan",
        "hour_replan",
    }
    tool_names = [tool["name"] for tool in payload["tools"]]
    assert "read_url" in tool_names
    assert "search_web" in tool_names
    assert payload["mcp_servers"] == []
    assert payload["latest_error"]["message"] is None
    assert payload["latest_error"]["latest_failed_execution"] is None


def test_runtime_plan_refresh_endpoint_rebuilds_and_overwrites_plan() -> None:
    memory_service, _ = build_in_memory_memory_service()
    persona_service, _ = build_in_memory_persona_service()
    app = create_app(
        memory_service=memory_service,
        persona_service=persona_service,
    )
    client = TestClient(app)

    first_response = client.post("/api/runtime/plan/refresh")
    second_response = client.post("/api/runtime/plan/refresh")

    assert first_response.status_code == 200
    assert second_response.status_code == 200

    first_payload = first_response.json()
    second_payload = second_response.json()

    assert first_payload["event_id"].startswith("evt_")
    assert second_payload["event_id"].startswith("evt_")
    assert first_payload["current_plan"]["minute_steps"]
    assert second_payload["current_plan"]["minute_steps"]
    assert first_payload["state"]["plan_summary"]
    assert second_payload["state"]["plan_summary"]
    assert memory_service.raw_entries
    planning_entries = [entry for entry in memory_service.raw_entries if entry.kind == "planning"]
    assert planning_entries
    assert planning_entries[-1].payload["trigger_event"]["event_type"] == "plan_refresh_requested"


def test_planner_lab_standalone_front_page_is_served() -> None:
    app = create_planner_lab_app()
    client = TestClient(app)

    response = client.get("/")

    assert response.status_code == 200
    assert "Plan / Replan Workbench" in response.text
    assert "/assets/planner-lab-standalone.js" in response.text


def test_plan_lab_endpoints_support_manual_day_start_and_replan() -> None:
    app = create_planner_lab_app()
    client = TestClient(app)

    clock = client.post(
        "/api/planner-lab/clock/set",
        json={"at": "2026-03-28T19:00:00+08:00"},
    )
    assert clock.status_code == 200

    day_start = client.post(
        "/api/planner-lab/day-start",
        json={"note": "manual day start"},
    )
    assert day_start.status_code == 200
    day_start_plan = day_start.json()["debug"]["current_plan"]
    assert day_start_plan["day_blocks"]

    target_block_id = day_start_plan["day_blocks"][-1]["block_id"]
    specific_expand = client.post(
        "/api/planner-lab/expand-block",
        json={"block_id": target_block_id},
    )
    assert specific_expand.status_code == 200
    specific_plan = specific_expand.json()["debug"]["current_plan"]
    assert specific_plan["active_block_id"] == target_block_id
    assert specific_plan["minute_steps"]

    shift_clock = client.post(
        "/api/planner-lab/clock/set",
        json={"at": "2026-03-28T21:00:00+08:00"},
    )
    assert shift_clock.status_code == 200

    expanded = client.post("/api/planner-lab/expand-ready-block", json={})
    assert expanded.status_code == 200
    expanded_steps = expanded.json()["debug"]["current_plan"]["minute_steps"]
    assert expanded_steps
    assert expanded_steps[0]["scheduled_for"].startswith("2026-03-28T21:00:00+08:00")

    decision = client.post(
        "/api/planner-lab/replan/decide",
        json={
            "outcome_status": "blocked_failure",
            "outcome_content": "The current block drifted off course.",
            "plan_exhausted": False,
        },
    )
    assert decision.status_code == 200
    assert decision.json()["decision"]["kind"] in {
        "no_replan",
        "micro_replan",
        "hour_replan",
    }

    applied = client.post(
        "/api/planner-lab/replan/apply",
        json={
            "kind": "micro_replan",
            "reason": "manual debug",
            "outcome_content": "Apply a local replanning pass.",
        },
    )
    assert applied.status_code == 200
    assert applied.json()["debug"]["current_plan"]["minute_steps"]

    debug_response = client.get("/api/planner-lab/debug?limit=5")
    assert debug_response.status_code == 200
    payload = debug_response.json()
    assert payload["current_plan"]["plan_date"]
    assert payload["planning_entries"]
    assert payload["replan_entries"]


def test_plan_lab_can_boot_in_paused_blank_state() -> None:
    app = create_planner_lab_app()
    with TestClient(app) as client:
        response = client.get("/api/planner-lab/debug")

        assert response.status_code == 200
        payload = response.json()
        assert payload["summary"]["clock_mode"] == "manual"
        assert payload["current_plan"]["plan_date"] is None
        assert payload["current_plan"]["day_blocks"] == []


def test_tool_debug_endpoint_surfaces_registry_and_recent_invocations() -> None:
    memory_service, _ = build_in_memory_memory_service()
    persona_service, _ = build_in_memory_persona_service()
    memory_service.record_outcome(
        PlanStep(
            step_id="step_tool_debug",
            title="Inspect a page and search for context",
            detail="Use builtin tools to inspect and search.",
        ),
        ActionOutcome(
            action_id="step_tool_debug",
            status=OutcomeStatus.PARTIAL_SUCCESS,
            mode=ExecutionMode.HYBRID,
            source=ExecutionZone.REAL,
            content="Inspected a page and then searched for follow-up context.",
            tool_invocations=[
                ToolInvocation(
                    capability="read_url",
                    arguments={"url": "https://example.com"},
                    status=OutcomeStatus.SUCCESS,
                    detail="Fetched the page successfully.",
                ),
                ToolInvocation(
                    capability="search_web",
                    arguments={"query": "example follow up"},
                    status=OutcomeStatus.SUCCESS,
                    detail="Found relevant follow-up results.",
                ),
            ],
            raw_data={
                "result": {
                    "tool_name": "read_url",
                    "content": [{"type": "text", "text": "example page"}],
                    "is_error": False,
                },
                "loop_tool_results": [
                    {
                        "tool_name": "search_web",
                        "content": [{"type": "text", "text": "search results"}],
                        "is_error": False,
                    }
                ],
            },
        ),
    )
    app = create_app(
        memory_service=memory_service,
        persona_service=persona_service,
        mcp_provider=StubMCPProvider(),
    )
    client = TestClient(app)

    response = client.get("/api/tools/debug", params={"limit": 5})

    assert response.status_code == 200
    payload = response.json()

    tool_names = [tool["name"] for tool in payload["tools"]]
    assert "read_url" in tool_names
    assert "search_web" in tool_names
    assert payload["mcp_servers"][0]["server_id"] == "stub-server"
    assert len(payload["recent_invocations"]) == 2
    assert payload["recent_invocations"][0]["tool_name"] == "read_url"
    assert payload["recent_invocations"][0]["step_id"] == "step_tool_debug"
    assert payload["recent_invocations"][0]["source_type"] == "internal"
    assert payload["recent_invocations"][0]["result"]["tool_name"] == "read_url"
    assert payload["recent_invocations"][1]["tool_name"] == "search_web"
    assert payload["recent_invocations"][1]["invocation_index"] == 2
    assert payload["recent_invocations"][1]["result"]["tool_name"] == "search_web"


def test_model_debug_endpoint_surfaces_recent_model_io_traces() -> None:
    memory_service, _ = build_in_memory_memory_service()
    persona_service, _ = build_in_memory_persona_service()
    memory_service.record_model_trace(
        ModelTracePayload(
            request_kind="structured",
            role="decision",
            provider="custom",
            provider_name="openai",
            model="planner-x",
            base_url="https://mock-llm.local/v1",
            schema_name="PlanDraft",
            prompt="final prompt body",
            system_prompt="system prompt body",
            output_object={"summary": "ok"},
            duration_ms=128,
            http_exchanges=[
                {
                    "request": {
                        "method": "POST",
                        "url": "https://mock-llm.local/v1/chat/completions",
                        "headers": {"authorization": "[redacted]"},
                        "body": '{"messages":[{"role":"user","content":"hello"}]}',
                    },
                    "response": {
                        "status_code": 200,
                        "headers": {"content-type": "application/json"},
                        "body": '{"id":"chatcmpl_1","choices":[]}',
                    },
                }
            ],
        )
    )
    app = create_app(
        memory_service=memory_service,
        persona_service=persona_service,
    )
    client = TestClient(app)

    response = client.get("/api/models/debug", params={"limit": 5})

    assert response.status_code == 200
    payload = response.json()
    assert payload["latest_trace"]["trace"]["prompt"] == "final prompt body"
    assert payload["latest_trace"]["trace"]["system_prompt"] == "system prompt body"
    assert payload["latest_trace"]["trace"]["schema_name"] == "PlanDraft"
    assert payload["latest_trace"]["trace"]["http_exchanges"][0]["request"]["method"] == "POST"
    assert payload["latest_trace"]["trace"]["http_exchanges"][0]["response"]["status_code"] == 200
    assert len(payload["recent_traces"]) == 1


def test_model_config_endpoint_reads_and_updates_routes(monkeypatch, tmp_path: Path) -> None:
    written_env: dict[str, str] = {}
    synced_env: dict[str, str] = {}

    def fake_update_project_env(values):
        written_env.update(values)
        return tmp_path / ".env"

    def fake_sync_process_env(values):
        synced_env.update(values)

    monkeypatch.setattr(main_module, "update_project_env", fake_update_project_env)
    monkeypatch.setattr(main_module, "sync_process_env", fake_sync_process_env)
    monkeypatch.setattr(main_module, "project_env_path", lambda: tmp_path / ".env")

    app = create_app()
    client = TestClient(app)

    initial = client.get("/api/models/config")
    assert initial.status_code == 200

    update = client.put(
        "/api/models/config",
        json={
            "dialogue": {
                "provider": "custom",
                "model": "dialogue-x",
                "api_key": "dlg-key",
                "base_url": "https://dialogue.local/v1",
            },
            "decision": {
                "provider": "anthropic",
                "model": "claude-test",
                "api_key": "dec-key",
                "base_url": "https://decision.local/v1",
            },
            "memory": {
                "provider": "openai",
                "model": "memory-x",
                "api_key": "mem-key",
                "base_url": "https://memory.local/v1",
            },
        },
    )

    assert update.status_code == 200
    payload = update.json()
    assert payload["dialogue"]["model"] == "dialogue-x"
    assert payload["decision"]["provider"] == "anthropic"
    assert payload["memory"]["base_url"] == "https://memory.local/v1"
    assert payload["env_path"] == str(tmp_path / ".env")
    assert written_env["AMADEUS_DIALOGUE_MODEL"] == "dialogue-x"
    assert written_env["AMADEUS_DECISION_API_KEY"] == "dec-key"
    assert synced_env["AMADEUS_MEMORY_BASE_URL"] == "https://memory.local/v1"


def test_model_connection_test_endpoint_uses_submitted_route() -> None:
    model_client = RecordingTextModelClient()
    app = create_app(model_client=model_client)
    client = TestClient(app)

    response = client.post(
        "/api/models/test",
        json={
            "role": "decision",
            "route": {
                "provider": "custom",
                "model": "planner-x",
                "api_key": "test-key",
                "base_url": "https://mock-llm.local/v1",
            },
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["provider_name"] == "stub-provider"
    assert payload["response_text"] == "OK"
    assert model_client.requests[0].role == ModelRole.DECISION
    assert model_client.requests[0].route.model == "planner-x"
    assert model_client.requests[0].route.base_url == "https://mock-llm.local/v1"


def test_health_endpoint_reports_runtime_scheduler_status() -> None:
    memory_service, _ = build_in_memory_memory_service()
    persona_service, _ = build_in_memory_persona_service()
    app = create_app(
        memory_service=memory_service,
        persona_service=persona_service,
    )

    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["scheduler_running"] is True
    assert payload["scheduler_paused"] is False
    assert payload["started_at"]
    assert payload["mcp_configured_server_count"] == 0
    assert payload["mcp_connected_server_count"] == 0
    assert payload["mcp_registered_tool_count"] == 0
    assert payload["mcp_servers"] == []


def test_health_and_runtime_state_surface_mcp_status() -> None:
    memory_service, _ = build_in_memory_memory_service()
    persona_service, _ = build_in_memory_persona_service()
    app = create_app(
        memory_service=memory_service,
        persona_service=persona_service,
        mcp_provider=StubMCPProvider(),
    )

    with TestClient(app) as client:
        health_response = client.get("/health")
        state_response = client.get("/api/runtime/state")

    assert health_response.status_code == 200
    assert state_response.status_code == 200

    health_payload = health_response.json()
    state_payload = state_response.json()

    assert health_payload["mcp_configured_server_count"] == 1
    assert health_payload["mcp_connected_server_count"] == 1
    assert health_payload["mcp_registered_tool_count"] == 2
    assert health_payload["mcp_servers"][0]["server_id"] == "stub-server"
    assert health_payload["mcp_servers"][0]["registered_tools"] == ["alpha", "beta"]
    assert state_payload["summary"]["mcp_configured_server_count"] == 1
    assert state_payload["summary"]["mcp_connected_server_count"] == 1
    assert state_payload["summary"]["mcp_registered_tool_count"] == 2
    assert state_payload["summary"]["mcp_servers"][0]["server_id"] == "stub-server"
    assert state_payload["summary"]["mcp_servers"][0]["registered_tools"] == ["alpha", "beta"]


def test_runtime_lifecycle_pause_and_resume_controls_scheduler() -> None:
    memory_service, _ = build_in_memory_memory_service()
    persona_service, _ = build_in_memory_persona_service()
    app = create_app(
        memory_service=memory_service,
        persona_service=persona_service,
    )

    with TestClient(app) as client:
        pause_response = client.post("/api/runtime/lifecycle/pause")
        paused_health = client.get("/health")
        paused_message = client.post(
            "/api/messages",
            json={
                "user_id": "user-pause",
                "channel": "api",
                "text": "manual message while paused",
            },
        )
        resume_response = client.post("/api/runtime/lifecycle/resume")
        resumed_health = client.get("/health")

    assert pause_response.status_code == 200
    pause_payload = pause_response.json()
    assert pause_payload["status"] == "paused"
    assert pause_payload["scheduler_running"] is False
    assert pause_payload["scheduler_paused"] is True
    assert pause_payload["state"]["runtime_status"] == "paused"
    assert pause_payload["state"]["scheduler_paused"] is True

    assert paused_health.status_code == 200
    paused_health_payload = paused_health.json()
    assert paused_health_payload["status"] == "paused"
    assert paused_health_payload["scheduler_running"] is False
    assert paused_health_payload["scheduler_paused"] is True

    assert paused_message.status_code == 200
    paused_message_payload = paused_message.json()
    assert paused_message_payload["outcome"]["status"] == "success"
    assert paused_message_payload["state"]["runtime_status"] == "paused"

    assert resume_response.status_code == 200
    resume_payload = resume_response.json()
    assert resume_payload["status"] == "ok"
    assert resume_payload["scheduler_running"] is True
    assert resume_payload["scheduler_paused"] is False
    assert resume_payload["state"]["scheduler_paused"] is False

    assert resumed_health.status_code == 200
    resumed_health_payload = resumed_health.json()
    assert resumed_health_payload["status"] == "ok"
    assert resumed_health_payload["scheduler_running"] is True
    assert resumed_health_payload["scheduler_paused"] is False


def test_create_app_restores_latest_runtime_snapshot() -> None:
    memory_harness = MemoryHarness()
    persona_harness = PersonaHarness()
    first_memory_service, _ = build_in_memory_memory_service(harness=memory_harness)
    first_persona_service, _ = build_in_memory_persona_service(harness=persona_harness)
    first_app = create_app(
        memory_service=first_memory_service,
        persona_service=first_persona_service,
    )
    first_client = TestClient(first_app)

    first_client.post(
        "/api/messages",
        json={
            "user_id": "user-2",
            "channel": "api",
            "text": "resume after restart",
        },
    )

    restarted_memory_service, _ = build_in_memory_memory_service(harness=memory_harness)
    restarted_persona_service, _ = build_in_memory_persona_service(harness=persona_harness)
    restarted_app = create_app(
        memory_service=restarted_memory_service,
        persona_service=restarted_persona_service,
    )
    restarted_client = TestClient(restarted_app)

    response = restarted_client.get("/api/runtime/state")

    assert response.status_code == 200
    payload = response.json()
    assert payload["state"]["plan"]["day_summary"] == "围绕新收到的消息调整短期计划。"
    assert payload["state"]["current_action_id"].startswith("step_")
    assert payload["latest_snapshot_id"].startswith("snap_")


def test_create_app_can_skip_restoring_latest_runtime_snapshot() -> None:
    memory_harness = MemoryHarness()
    persona_harness = PersonaHarness()
    first_memory_service, _ = build_in_memory_memory_service(harness=memory_harness)
    first_persona_service, _ = build_in_memory_persona_service(harness=persona_harness)
    first_app = create_app(
        memory_service=first_memory_service,
        persona_service=first_persona_service,
    )
    first_client = TestClient(first_app)

    first_client.post(
        "/api/messages",
        json={
            "user_id": "user-3",
            "channel": "api",
            "text": "leave a snapshot behind",
        },
    )

    restarted_memory_service, _ = build_in_memory_memory_service(harness=memory_harness)
    restarted_persona_service, _ = build_in_memory_persona_service(harness=persona_harness)
    restarted_app = create_app(
        memory_service=restarted_memory_service,
        persona_service=restarted_persona_service,
        auto_start_scheduler=False,
        restore_runtime_state=False,
    )

    with TestClient(restarted_app) as restarted_client:
        response = restarted_client.get("/api/runtime/state")

    assert response.status_code == 200
    payload = response.json()
    assert payload["state"]["plan"]["plan_date"] is None
    assert payload["state"]["plan"]["day_blocks"] == []
    assert payload["state"]["current_action_id"] is None
    assert payload["latest_snapshot_id"].startswith("snap_")


def test_memory_search_endpoint_returns_archive_fallback() -> None:
    memory_service, _ = build_in_memory_memory_service(
        harness=MemoryHarness(
            active_store=InMemoryJsonlStore(
                [
                    ActiveMemoryEntry(
                        created_at="2020-01-01T09:00:00+00:00",
                        content="hello amadeus from an older day",
                        source="interaction",
                    ).model_dump(mode="json"),
                    ActiveMemoryEntry(
                        created_at="2099-03-26T09:00:00+00:00",
                        content="hello amadeus from a fresh day",
                        source="interaction",
                    ).model_dump(mode="json")
                ]
            )
        ),
        active_retention_days=1,
    )
    persona_service, _ = build_in_memory_persona_service()
    app = create_app(
        memory_service=memory_service,
        persona_service=persona_service,
    )
    client = TestClient(app)

    memory_response = client.get("/api/memory", params={"limit": 5})
    search_response = client.get(
        "/api/memory/search",
        params={"query": "hello amadeus", "top_k": 5},
    )

    assert memory_response.status_code == 200
    assert search_response.status_code == 200

    memory_payload = memory_response.json()
    search_payload = search_response.json()

    assert memory_payload["archive_entries"]
    assert search_payload["active_hits"]
    assert search_payload["archive_hits"]


def test_memory_debug_search_endpoint_surfaces_retrieval_stages() -> None:
    memory_service, _ = build_in_memory_memory_service(
        harness=MemoryHarness(
            active_store=InMemoryJsonlStore(
                [
                    ActiveMemoryEntry(
                        entry_id="mem-semantic",
                        created_at="2026-03-27T09:00:00+00:00",
                        content="project continuity handoff",
                        source="interaction",
                        semantic_embedding=[1.0, 0.0],
                    ).model_dump(mode="json"),
                    ActiveMemoryEntry(
                        entry_id="mem-bm25",
                        created_at="2026-03-27T09:05:00+00:00",
                        content="deadline current thread",
                        source="interaction",
                        semantic_embedding=[0.0, 1.0],
                    ).model_dump(mode="json"),
                ]
            )
        ),
        semantic_query_embedder=lambda _: [1.0, 0.0],
    )
    persona_service, _ = build_in_memory_persona_service()
    app = create_app(
        memory_service=memory_service,
        persona_service=persona_service,
    )
    client = TestClient(app)

    response = client.get(
        "/api/memory/debug/search",
        params={"query": "deadline current thread", "top_k": 2},
    )

    assert response.status_code == 200
    payload = response.json()

    assert payload["query"] == "deadline current thread"
    assert payload["active"]["settings"]["semantic_enabled"] is True
    assert payload["active"]["settings"]["bm25_enabled"] is True
    assert payload["active"]["stage_hits"]["semantic"]
    assert payload["active"]["stage_hits"]["bm25"]
    candidate_ids = [
        candidate["entry_id"] for candidate in payload["active"]["combined_candidates"]
    ]
    assert "mem-semantic" in candidate_ids
    assert "mem-bm25" in candidate_ids
    assert payload["active"]["final_entries"]


def test_persona_bootstrap_persists_profile_and_updates_runtime() -> None:
    memory_harness = MemoryHarness()
    persona_harness = PersonaHarness()
    memory_service, _ = build_in_memory_memory_service(harness=memory_harness)
    persona_service, _ = build_in_memory_persona_service(harness=persona_harness)
    app = create_app(
        memory_service=memory_service,
        persona_service=persona_service,
    )
    client = TestClient(app)

    response = client.post(
        "/api/persona/bootstrap",
        json={
            "name": "Kurisu",
            "seed_text": (
                "A sharp, curious researcher who likes quiet routines and careful planning."
            ),
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["profile"]["name"] == "Kurisu"
    assert payload["core_memory"]["soul_md"].startswith("# 灵魂档案：Kurisu")

    restarted_memory_service, _ = build_in_memory_memory_service(harness=memory_harness)
    restarted_persona_service, _ = build_in_memory_persona_service(harness=persona_harness)
    restarted_app = create_app(
        memory_service=restarted_memory_service,
        persona_service=restarted_persona_service,
    )
    restarted_client = TestClient(restarted_app)

    persona_response = restarted_client.get("/api/persona")
    state_response = restarted_client.get("/api/runtime/state")

    assert persona_response.status_code == 200
    assert state_response.status_code == 200
    assert persona_response.json()["profile"]["name"] == "Kurisu"
    assert persona_response.json()["core_memory"]["soul_md"].startswith("# 灵魂档案：Kurisu")
    assert state_response.json()["state"]["persona_summary"].startswith(
        "A sharp, curious researcher"
    )


def test_persona_card_endpoints_support_direct_soul_edit(tmp_path: Path) -> None:
    registry = PersonaRegistry(
        index_path=tmp_path / "personas" / "index.json",
        workspace_root=tmp_path / "personas",
    )
    app = create_app(
        persona_registry=registry,
    )
    client = TestClient(app)

    create_response = client.post(
        "/api/personas",
        json={"name": "Kurisu", "activate": True},
    )
    assert create_response.status_code == 200
    assert create_response.json()["card"]["persona_key"] == "kurisu"

    bootstrap_response = client.post(
        "/api/personas/kurisu/bootstrap",
        json={
            "name": "Kurisu",
            "seed_text": "A quiet, reflective persona who likes reading and tea.",
        },
    )
    assert bootstrap_response.status_code == 200

    soul_response = client.put(
        "/api/personas/kurisu/soul",
        json={
            "soul_md": (
                "# Soul: Kurisu\n\n"
                "## Core\n"
                "A sharper, more assertive researcher persona.\n\n"
                "Edited manually."
            )
        },
    )

    assert soul_response.status_code == 200
    soul_payload = soul_response.json()
    assert soul_payload["profile"]["name"] == "Kurisu"
    assert soul_payload["core_memory"]["soul_md"].startswith("# 灵魂档案：Kurisu")

def test_persona_card_activation_isolates_runtime_files(tmp_path: Path) -> None:
    registry = PersonaRegistry(
        index_path=tmp_path / "personas" / "index.json",
        workspace_root=tmp_path / "personas",
    )
    app = create_app(
        persona_registry=registry,
    )
    client = TestClient(app)

    client.post("/api/personas", json={"name": "Kurisu", "activate": True})
    client.post(
        "/api/personas/kurisu/bootstrap",
        json={
            "name": "Kurisu",
            "seed_text": "A careful researcher who likes quiet routines.",
        },
    )
    assert (tmp_path / "personas" / "kurisu" / "soul.md").exists()
    assert (tmp_path / "personas" / "kurisu" / "core_memory.json").exists()
    assert (tmp_path / "personas" / "kurisu" / "snapshots.jsonl").exists()

    client.post("/api/personas", json={"name": "Mayuri", "activate": False})
    client.post(
        "/api/personas/mayuri/bootstrap",
        json={
            "name": "Mayuri",
            "seed_text": "A warm, playful persona who keeps a gentle daily rhythm.",
        },
    )
    activate_response = client.post("/api/personas/mayuri/activate")
    assert activate_response.status_code == 200
    assert activate_response.json()["card"]["persona_key"] == "mayuri"
    assert activate_response.json()["core_memory"]["soul_md"].startswith("# 灵魂档案：Mayuri")

    assert (tmp_path / "personas" / "mayuri" / "soul.md").exists()
    assert (tmp_path / "personas" / "mayuri" / "core_memory.json").exists()

    list_response = client.get("/api/personas")
    assert list_response.status_code == 200
    assert list_response.json()["active_persona_key"] == "mayuri"

    kurisu_detail = client.get("/api/personas/kurisu")
    mayuri_detail = client.get("/api/personas/mayuri")
    assert kurisu_detail.status_code == 200
    assert mayuri_detail.status_code == 200
    assert kurisu_detail.json()["card"]["name"] == "Kurisu"
    assert mayuri_detail.json()["card"]["name"] == "Mayuri"
    assert kurisu_detail.json()["profile"]["name"] == "Kurisu"
    assert mayuri_detail.json()["profile"]["name"] == "Mayuri"


def test_persona_management_front_page_is_removed(tmp_path: Path) -> None:
    registry = PersonaRegistry(
        index_path=tmp_path / "personas" / "index.json",
        workspace_root=tmp_path / "personas",
    )
    app = create_app(persona_registry=registry)
    client = TestClient(app)

    response = client.get("/front/personas")

    assert response.status_code == 404


def test_executor_lab_front_page_is_served(tmp_path: Path) -> None:
    registry = PersonaRegistry(
        index_path=tmp_path / "personas" / "index.json",
        workspace_root=tmp_path / "personas",
    )
    app = create_app(persona_registry=registry)
    client = TestClient(app)

    root_response = client.get("/")
    debug_response = client.get("/front/debug")
    executor_response = client.get("/front/executor-lab")

    assert root_response.status_code == 200
    assert debug_response.status_code == 200
    assert executor_response.status_code == 200
    assert "Executor Lab" in debug_response.text
    assert "Executor Lab" in executor_response.text
    assert "/assets/executor-lab.js" in executor_response.text
    assert "Executor Lab" in root_response.text


def test_settings_front_page_is_removed(tmp_path: Path) -> None:
    registry = PersonaRegistry(
        index_path=tmp_path / "personas" / "index.json",
        workspace_root=tmp_path / "personas",
    )
    app = create_app(persona_registry=registry)
    client = TestClient(app)

    response = client.get("/front/settings")

    assert response.status_code == 404


def test_prompt_editor_api_remains_but_front_page_is_removed(tmp_path: Path) -> None:
    prompt_root = tmp_path / "prompts"
    prompt_file = prompt_root / "runtime" / "planning" / "autonomous_system.txt"
    prompt_file.parent.mkdir(parents=True, exist_ok=True)
    prompt_file.write_text("测试 Prompt 内容", encoding="utf-8")

    app = create_app(prompt_root=prompt_root)
    client = TestClient(app)

    page_response = client.get("/front/prompts")
    list_response = client.get("/api/prompts")
    file_response = client.get(
        "/api/prompts/file",
        params={"path": "runtime/planning/autonomous_system.txt"},
    )

    assert page_response.status_code == 404
    assert list_response.status_code == 200
    assert list_response.json()[0]["path"] == "runtime/planning/autonomous_system.txt"
    assert file_response.status_code == 200
    assert file_response.json()["content"] == "测试 Prompt 内容"


def test_prompt_editor_can_update_prompt_file(tmp_path: Path) -> None:
    prompt_root = tmp_path / "prompts"
    prompt_file = prompt_root / "runtime" / "planning" / "autonomous_system.txt"
    prompt_file.parent.mkdir(parents=True, exist_ok=True)
    prompt_file.write_text("旧内容", encoding="utf-8")

    app = create_app(prompt_root=prompt_root)
    client = TestClient(app)

    update_response = client.put(
        "/api/prompts/file",
        params={"path": "runtime/planning/autonomous_system.txt"},
        json={"content": "新内容"},
    )

    assert update_response.status_code == 200
    assert update_response.json()["content"] == "新内容"
    assert prompt_file.read_text(encoding="utf-8") == "新内容"


def test_deleting_active_persona_promotes_next_card(tmp_path: Path) -> None:
    registry = PersonaRegistry(
        index_path=tmp_path / "personas" / "index.json",
        workspace_root=tmp_path / "personas",
    )
    app = create_app(persona_registry=registry)
    client = TestClient(app)

    client.post("/api/personas", json={"name": "Kurisu", "activate": True})
    client.post(
        "/api/personas/kurisu/bootstrap",
        json={"name": "Kurisu", "seed_text": "A careful researcher."},
    )
    client.post("/api/personas", json={"name": "Mayuri", "activate": False})
    client.post(
        "/api/personas/mayuri/bootstrap",
        json={"name": "Mayuri", "seed_text": "A warm persona."},
    )

    response = client.delete("/api/personas/kurisu")

    assert response.status_code == 200
    payload = response.json()
    assert payload["active_persona_key"] == "mayuri"
    assert [card["persona_key"] for card in payload["cards"]] == ["mayuri"]
    assert not (tmp_path / "personas" / "kurisu").exists()
