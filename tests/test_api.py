from fastapi.testclient import TestClient

from app.communication.qq import QQBotSettings
from app.main import create_app
from tests.test_support import (
    MemoryHarness,
    PersonaHarness,
    build_in_memory_memory_service,
    build_in_memory_persona_service,
)


def disabled_qq_settings() -> QQBotSettings:
    return QQBotSettings(enabled=False)


def test_post_message_runs_single_cycle() -> None:
    memory_service, _ = build_in_memory_memory_service()
    persona_service, _ = build_in_memory_persona_service()
    app = create_app(
        memory_service=memory_service,
        persona_service=persona_service,
        qq_settings=disabled_qq_settings(),
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

    assert response.status_code == 200
    payload = response.json()
    assert payload["event_id"].startswith("evt_")
    assert payload["outcome"]["status"] == "success"
    assert payload["outbound_messages"]
    assert payload["state"]["emotion_summary"] == "steady and quietly positive"
    assert payload["state"]["next_wake_at"]
    assert payload["state"]["next_step_scheduled_for"]


def test_runtime_and_memory_inspection_endpoints() -> None:
    memory_service, _ = build_in_memory_memory_service()
    persona_service, _ = build_in_memory_persona_service()
    app = create_app(
        memory_service=memory_service,
        persona_service=persona_service,
        qq_settings=disabled_qq_settings(),
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
    assert memory_payload["core_memory"]["current_emotion"]["summary"] == (
        "steady and quietly positive"
    )
    assert memory_payload["active_entries"]
    assert memory_payload["raw_entries"]


def test_create_app_restores_latest_runtime_snapshot() -> None:
    memory_harness = MemoryHarness()
    persona_harness = PersonaHarness()
    first_memory_service, _ = build_in_memory_memory_service(harness=memory_harness)
    first_persona_service, _ = build_in_memory_persona_service(harness=persona_harness)
    first_app = create_app(
        memory_service=first_memory_service,
        persona_service=first_persona_service,
        qq_settings=disabled_qq_settings(),
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
        qq_settings=disabled_qq_settings(),
    )
    restarted_client = TestClient(restarted_app)

    response = restarted_client.get("/api/runtime/state")

    assert response.status_code == 200
    payload = response.json()
    assert payload["state"]["plan"]["day_summary"] == (
        "Adapt the short-term plan around the new inbound message."
    )
    assert payload["state"]["current_action_id"].startswith("step_")
    assert payload["latest_snapshot_id"].startswith("snap_")


def test_memory_search_endpoint_returns_archive_fallback() -> None:
    memory_service, _ = build_in_memory_memory_service(
        max_active_entries=2,
        archive_batch_size=1,
    )
    persona_service, _ = build_in_memory_persona_service()
    app = create_app(
        memory_service=memory_service,
        persona_service=persona_service,
        qq_settings=disabled_qq_settings(),
    )
    client = TestClient(app)

    for _ in range(3):
        client.post(
            "/api/messages",
            json={
                "user_id": "user-search",
                "channel": "api",
                "text": "hello amadeus",
            },
        )

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


def test_persona_bootstrap_persists_profile_and_updates_runtime() -> None:
    memory_harness = MemoryHarness()
    persona_harness = PersonaHarness()
    memory_service, _ = build_in_memory_memory_service(harness=memory_harness)
    persona_service, _ = build_in_memory_persona_service(harness=persona_harness)
    app = create_app(
        memory_service=memory_service,
        persona_service=persona_service,
        qq_settings=disabled_qq_settings(),
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
    assert payload["core_memory"]["persona_summary"].startswith("A sharp, curious researcher")

    restarted_memory_service, _ = build_in_memory_memory_service(harness=memory_harness)
    restarted_persona_service, _ = build_in_memory_persona_service(harness=persona_harness)
    restarted_app = create_app(
        memory_service=restarted_memory_service,
        persona_service=restarted_persona_service,
        qq_settings=disabled_qq_settings(),
    )
    restarted_client = TestClient(restarted_app)

    persona_response = restarted_client.get("/api/persona")
    state_response = restarted_client.get("/api/runtime/state")

    assert persona_response.status_code == 200
    assert state_response.status_code == 200
    assert persona_response.json()["profile"]["name"] == "Kurisu"
    assert state_response.json()["state"]["persona_summary"].startswith(
        "A sharp, curious researcher"
    )
