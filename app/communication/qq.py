from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

import httpx
from pydantic import BaseModel, Field
from websockets.asyncio.client import connect as websocket_connect

from app.communication.hub import CommunicationHub
from app.core.events import EventSource, EventType, RuntimeEvent
from app.runtime.orchestrator import RuntimeOrchestrator

QQ_INTENT_C2C_MESSAGES = 1 << 25
QQ_OP_DISPATCH = 0
QQ_OP_HEARTBEAT = 1
QQ_OP_IDENTIFY = 2
QQ_OP_RESUME = 6
QQ_OP_RECONNECT = 7
QQ_OP_INVALID_SESSION = 9
QQ_OP_HELLO = 10
QQ_OP_HEARTBEAT_ACK = 11


class QQBotSettings(BaseModel):
    enabled: bool = False
    app_id: str = ""
    app_secret: str = ""
    access_token_url: str = "https://bots.qq.com/app/getAppAccessToken"
    api_base_url: str = "https://api.sgroup.qq.com"
    sandbox_api_base_url: str = "https://sandbox.api.sgroup.qq.com"
    use_sandbox: bool = False
    reconnect_base_delay_seconds: float = 1.0
    reconnect_max_delay_seconds: float = 60.0
    heartbeat_fallback_seconds: float = 45.0

    @property
    def message_api_base_url(self) -> str:
        if self.use_sandbox:
            return self.sandbox_api_base_url.rstrip("/")
        return self.api_base_url.rstrip("/")

    @property
    def gateway_url_endpoint(self) -> str:
        return f"{self.api_base_url.rstrip('/')}/gateway"

    @property
    def is_configured(self) -> bool:
        return bool(self.app_id and self.app_secret)

    @property
    def should_start(self) -> bool:
        return self.enabled and self.is_configured

    @classmethod
    def from_env(cls) -> QQBotSettings:
        import os

        return cls(
            enabled=os.getenv("AMADEUS_QQ_ENABLED", "false").lower() == "true",
            app_id=os.getenv("AMADEUS_QQ_APP_ID", ""),
            app_secret=os.getenv("AMADEUS_QQ_APP_SECRET", ""),
            access_token_url=os.getenv(
                "AMADEUS_QQ_ACCESS_TOKEN_URL",
                "https://bots.qq.com/app/getAppAccessToken",
            ),
            api_base_url=os.getenv("AMADEUS_QQ_API_BASE_URL", "https://api.sgroup.qq.com"),
            sandbox_api_base_url=os.getenv(
                "AMADEUS_QQ_SANDBOX_API_BASE_URL",
                "https://sandbox.api.sgroup.qq.com",
            ),
            use_sandbox=os.getenv("AMADEUS_QQ_USE_SANDBOX", "false").lower() == "true",
        )


class QQGatewayPayload(BaseModel):
    op: int
    d: Any = None
    s: int | None = None
    t: str | None = None
    id: str | None = None


class QQMessageAuthor(BaseModel):
    id: str | None = None
    user_openid: str | None = None
    username: str | None = None
    bot: bool = False


class QQC2CMessageData(BaseModel):
    id: str
    content: str = ""
    timestamp: str | None = None
    author: QQMessageAuthor = Field(default_factory=QQMessageAuthor)

    @property
    def sender_openid(self) -> str | None:
        return self.author.user_openid or self.author.id


class QQAccessTokenResponse(BaseModel):
    access_token: str
    expires_in: int


class QQGatewayWebSocket(Protocol):
    async def send(self, data: str) -> None: ...

    async def recv(self) -> str | bytes: ...

    async def close(self, code: int = 1000, reason: str = "") -> None: ...


class QQReconnectRequested(Exception):
    """Raised when the QQ gateway asks the client to reconnect."""


def build_identify_payload(token: str) -> dict[str, Any]:
    return {
        "op": QQ_OP_IDENTIFY,
        "d": {
            "token": f"QQBot {token}",
            "intents": QQ_INTENT_C2C_MESSAGES,
            "shard": [0, 1],
        },
    }


def build_resume_payload(token: str, session_id: str, sequence: int) -> dict[str, Any]:
    return {
        "op": QQ_OP_RESUME,
        "d": {
            "token": f"QQBot {token}",
            "session_id": session_id,
            "seq": sequence,
        },
    }


def build_heartbeat_payload(sequence: int | None) -> dict[str, Any]:
    return {"op": QQ_OP_HEARTBEAT, "d": sequence}


class QQAdapter:
    def __init__(
        self,
        settings: QQBotSettings,
        orchestrator: RuntimeOrchestrator,
        communication_hub: CommunicationHub,
        http_client: httpx.AsyncClient | None = None,
        websocket_connect_factory: Callable[[str], Any] | None = None,
    ) -> None:
        self.settings = settings
        self.orchestrator = orchestrator
        self.communication_hub = communication_hub
        self.http_client = http_client
        self.websocket_connect_factory = (
            websocket_connect_factory
            or (
                lambda url: websocket_connect(
                    url,
                    open_timeout=10,
                    ping_interval=None,
                    close_timeout=5,
                )
            )
        )
        self._access_token: str | None = None
        self._access_token_expires_at: datetime | None = None
        self._heartbeat_task: asyncio.Task[None] | None = None
        self._runner_task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()
        self._websocket: QQGatewayWebSocket | None = None
        self._last_sequence: int | None = None
        self._session_id: str | None = None
        self._seen_message_ids: dict[str, None] = {}
        self._message_sequences: dict[str, int] = {}

    @property
    def is_configured(self) -> bool:
        return self.settings.is_configured

    @property
    def is_running(self) -> bool:
        return self._runner_task is not None and not self._runner_task.done()

    async def start(self) -> None:
        if not self.settings.should_start or self.is_running:
            return

        self._stop_event = asyncio.Event()
        self._runner_task = asyncio.create_task(self._run_forever())

    async def stop(self) -> None:
        self._stop_event.set()
        self._cancel_heartbeat()

        websocket = self._websocket
        self._websocket = None
        if websocket is not None:
            with suppress(Exception):
                await websocket.close(code=1000, reason="adapter stopping")

        if self._runner_task is not None:
            self._runner_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._runner_task
            self._runner_task = None

    async def _run_forever(self) -> None:
        attempt = 0
        while not self._stop_event.is_set():
            try:
                token = await self._get_access_token()
                gateway_url = await self._get_gateway_url(token)
                await self._run_gateway_session(gateway_url, token)
                attempt = 0
            except asyncio.CancelledError:
                raise
            except QQReconnectRequested:
                pass
            except Exception as exc:
                print(f"[amadeus.qq] gateway loop error: {exc}")
            finally:
                self._cancel_heartbeat()
                self._websocket = None

            if self._stop_event.is_set():
                break

            attempt += 1
            delay = min(
                self.settings.reconnect_base_delay_seconds * (2 ** max(attempt - 1, 0)),
                self.settings.reconnect_max_delay_seconds,
            )
            await asyncio.sleep(delay)

    async def _run_gateway_session(self, gateway_url: str, token: str) -> None:
        async with self.websocket_connect_factory(gateway_url) as websocket:
            self._websocket = websocket
            while not self._stop_event.is_set():
                try:
                    raw_message = await websocket.recv()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    if self._stop_event.is_set():
                        return
                    raise

                payload = self._parse_gateway_payload(raw_message)
                await self.handle_gateway_payload(payload, token=token, websocket=websocket)

    def _parse_gateway_payload(self, raw_message: str | bytes) -> QQGatewayPayload:
        if isinstance(raw_message, bytes):
            raw_message = raw_message.decode("utf-8")
        return QQGatewayPayload.model_validate_json(raw_message)

    async def handle_gateway_payload(
        self,
        payload: QQGatewayPayload,
        *,
        token: str,
        websocket: QQGatewayWebSocket,
    ) -> None:
        if payload.op == QQ_OP_HELLO:
            heartbeat_interval_ms = self.settings.heartbeat_fallback_seconds * 1000
            if isinstance(payload.d, dict):
                heartbeat_interval_ms = payload.d.get(
                    "heartbeat_interval",
                    heartbeat_interval_ms,
                )
            self._start_heartbeat(websocket, heartbeat_interval_ms / 1000)

            if self._session_id and self._last_sequence is not None:
                resume_payload = build_resume_payload(
                    token,
                    self._session_id,
                    self._last_sequence,
                )
                await websocket.send(json.dumps(resume_payload))
            else:
                await websocket.send(json.dumps(build_identify_payload(token)))
            return

        if payload.op == QQ_OP_DISPATCH:
            if payload.s is not None:
                self._last_sequence = payload.s

            if payload.t == "READY":
                if isinstance(payload.d, dict):
                    self._session_id = payload.d.get("session_id")
                return

            if payload.t == "RESUMED":
                return

            if payload.t == "C2C_MESSAGE_CREATE":
                message = QQC2CMessageData.model_validate(payload.d)
                await self._process_c2c_message(message)
                return

        if payload.op == QQ_OP_HEARTBEAT_ACK:
            return

        if payload.op == QQ_OP_RECONNECT:
            raise QQReconnectRequested("QQ gateway requested reconnect")

        if payload.op == QQ_OP_INVALID_SESSION:
            self._session_id = None
            self._last_sequence = None
            raise QQReconnectRequested("QQ gateway invalidated the current session")

    async def _process_c2c_message(self, message: QQC2CMessageData) -> None:
        if message.author.bot:
            return

        user_openid = message.sender_openid
        if not user_openid:
            return

        if message.id in self._seen_message_ids:
            return

        self._remember_seen_message(message.id)

        runtime_event = RuntimeEvent(
            event_type=EventType.MESSAGE_RECEIVED,
            source=EventSource.CHANNEL,
            payload={
                "user_id": user_openid,
                "channel": "qq",
                "qq_event_type": "C2C_MESSAGE_CREATE",
                "qq_message_id": message.id,
                "qq_user_openid": user_openid,
                "text": message.content,
            },
        )
        await self.orchestrator.enqueue(runtime_event)
        await self.orchestrator.run_once()
        await self.send_outbound_messages(
            user_openid=user_openid,
            source_message_id=message.id,
        )

    def _remember_seen_message(self, message_id: str) -> None:
        self._seen_message_ids[message_id] = None
        while len(self._seen_message_ids) > 1000:
            oldest = next(iter(self._seen_message_ids))
            self._seen_message_ids.pop(oldest, None)

    def _next_message_sequence(self, source_message_id: str) -> int:
        next_sequence = self._message_sequences.get(source_message_id, 0) + 1
        self._message_sequences[source_message_id] = next_sequence
        while len(self._message_sequences) > 500:
            oldest = next(iter(self._message_sequences))
            self._message_sequences.pop(oldest, None)
        return next_sequence

    async def send_outbound_messages(
        self,
        user_openid: str | None,
        source_message_id: str,
    ) -> None:
        if not user_openid:
            self.communication_hub.drain_outbox()
            return

        messages = self.communication_hub.drain_outbox()
        for message in messages:
            await self._send_c2c_message(
                user_openid=user_openid,
                content=message.content,
                source_message_id=source_message_id,
                msg_seq=self._next_message_sequence(source_message_id),
            )

    async def _send_c2c_message(
        self,
        user_openid: str,
        content: str,
        source_message_id: str,
        msg_seq: int,
    ) -> None:
        access_token = await self._get_access_token()
        url = f"{self.settings.message_api_base_url}/v2/users/{user_openid}/messages"
        payload = {
            "content": content,
            "msg_type": 0,
            "msg_id": source_message_id,
            "msg_seq": msg_seq,
        }
        headers = {
            "Authorization": f"QQBot {access_token}",
            "X-Union-Appid": self.settings.app_id,
        }
        response = await self._post_json(url=url, payload=payload, headers=headers)
        response.raise_for_status()

    async def _get_access_token(self) -> str:
        now = datetime.now(UTC)
        if (
            self._access_token is not None
            and self._access_token_expires_at is not None
            and now < self._access_token_expires_at
        ):
            return self._access_token

        response = await self._post_json(
            url=self.settings.access_token_url,
            payload={
                "appId": self.settings.app_id,
                "clientSecret": self.settings.app_secret,
            },
        )
        response.raise_for_status()
        token = QQAccessTokenResponse.model_validate(response.json())
        self._access_token = token.access_token
        self._access_token_expires_at = now + timedelta(seconds=max(token.expires_in - 60, 60))
        return self._access_token

    async def _get_gateway_url(self, access_token: str) -> str:
        headers = {"Authorization": f"QQBot {access_token}"}
        response = await self._get(url=self.settings.gateway_url_endpoint, headers=headers)
        response.raise_for_status()
        payload = response.json()
        return str(payload["url"])

    def _start_heartbeat(
        self,
        websocket: QQGatewayWebSocket,
        interval_seconds: float,
    ) -> None:
        self._cancel_heartbeat()
        self._heartbeat_task = asyncio.create_task(
            self._heartbeat_loop(websocket, interval_seconds)
        )

    async def _heartbeat_loop(
        self,
        websocket: QQGatewayWebSocket,
        interval_seconds: float,
    ) -> None:
        try:
            while not self._stop_event.is_set():
                await asyncio.sleep(interval_seconds)
                payload = build_heartbeat_payload(self._last_sequence)
                await websocket.send(json.dumps(payload))
        except asyncio.CancelledError:
            raise
        except Exception:
            if not self._stop_event.is_set():
                raise

    def _cancel_heartbeat(self) -> None:
        if self._heartbeat_task is not None:
            self._heartbeat_task.cancel()
            self._heartbeat_task = None

    async def _get(
        self,
        url: str,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        if self.http_client is not None:
            return await self.http_client.get(url, headers=headers)

        async with httpx.AsyncClient(timeout=10.0) as client:
            return await client.get(url, headers=headers)

    async def _post_json(
        self,
        url: str,
        payload: dict[str, Any],
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        if self.http_client is not None:
            return await self.http_client.post(url, json=payload, headers=headers)

        async with httpx.AsyncClient(timeout=10.0) as client:
            return await client.post(url, json=payload, headers=headers)
