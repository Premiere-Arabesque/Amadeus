import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
import pytest
from fastapi.testclient import TestClient

from app.communication.hub import CommunicationHub
from app.communication.qq import QQAdapter, QQBotSettings
from app.main import build_orchestrator, create_app
from app.memory.service import MemoryService


class FakeWebSocket:
    def __init__(self, messages: list[str]) -> None:
        self._messages: asyncio.Queue[str | BaseException] = asyncio.Queue()
        for message in messages:
            self._messages.put_nowait(message)
        self.sent_payloads: list[dict] = []
        self.closed = False

    async def send(self, data: str) -> None:
        self.sent_payloads.append(json.loads(data))

    async def recv(self) -> str:
        item = await self._messages.get()
        if isinstance(item, BaseException):
            raise item
        return item

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.closed = True
        self._messages.put_nowait(EOFError(f"{code}:{reason}"))


def build_memory_service(tmp_path) -> MemoryService:
    return MemoryService(
        raw_log_path=tmp_path / "raw_logs.jsonl",
        snapshot_path=tmp_path / "snapshots.jsonl",
        active_memory_path=tmp_path / "active_memory.jsonl",
        core_memory_path=tmp_path / "core_memory.json",
        archive_memory_path=tmp_path / "archive_memory.jsonl",
    )


@pytest.mark.anyio
async def test_qq_adapter_processes_gateway_message_and_replies(tmp_path) -> None:
    sent_requests: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url == httpx.URL("https://bots.qq.com/app/getAppAccessToken"):
            return httpx.Response(
                200,
                json={"access_token": "token-xyz", "expires_in": 7200},
            )

        if request.url == httpx.URL("https://api.sgroup.qq.com/gateway"):
            return httpx.Response(200, json={"url": "wss://gateway.example"})

        sent_requests.append((str(request.url), request.read().decode("utf-8")))
        return httpx.Response(200, json={"id": "reply-1"})

    websocket = FakeWebSocket(
        messages=[
            json.dumps({"op": 10, "d": {"heartbeat_interval": 60_000}}),
            json.dumps(
                {
                    "op": 0,
                    "s": 1,
                    "t": "READY",
                    "d": {"session_id": "session-1"},
                }
            ),
            json.dumps(
                {
                    "op": 0,
                    "s": 2,
                    "t": "C2C_MESSAGE_CREATE",
                    "d": {
                        "id": "msg-1",
                        "content": "hello",
                        "author": {"user_openid": "openid-1", "bot": False},
                    },
                }
            ),
        ]
    )

    @asynccontextmanager
    async def fake_connect(url: str) -> AsyncIterator[FakeWebSocket]:
        assert url == "wss://gateway.example"
        yield websocket

    transport = httpx.MockTransport(handler)
    settings = QQBotSettings(
        enabled=True,
        app_id="123",
        app_secret="secret-1234567890",
    )
    communication_hub = CommunicationHub()
    memory_service = build_memory_service(tmp_path)
    adapter = QQAdapter(
        settings=settings,
        orchestrator=build_orchestrator(
            communication_hub=communication_hub,
            memory_service=memory_service,
        ),
        communication_hub=communication_hub,
        http_client=httpx.AsyncClient(transport=transport),
        websocket_connect_factory=fake_connect,
    )

    await adapter.start()

    async def wait_until_processed() -> None:
        for _ in range(100):
            if sent_requests:
                return
            await asyncio.sleep(0.01)
        raise AssertionError("Timed out waiting for QQ outbound reply")

    await wait_until_processed()
    await adapter.stop()

    assert websocket.sent_payloads
    assert websocket.sent_payloads[0]["op"] == 2
    assert sent_requests
    assert sent_requests[0][0].endswith("/v2/users/openid-1/messages")


def test_create_app_lifespan_starts_and_stops_qq_adapter(tmp_path) -> None:
    app = create_app(
        memory_service=build_memory_service(tmp_path),
        qq_settings=QQBotSettings(
            enabled=True,
            app_id="123",
            app_secret="secret-1234567890",
        ),
    )
    adapter = app.state.qq_adapter
    calls: list[str] = []

    async def fake_start() -> None:
        calls.append("start")

    async def fake_stop() -> None:
        calls.append("stop")

    adapter.start = fake_start  # type: ignore[method-assign]
    adapter.stop = fake_stop  # type: ignore[method-assign]

    with TestClient(app):
        assert calls == ["start"]

    assert calls == ["start", "stop"]
