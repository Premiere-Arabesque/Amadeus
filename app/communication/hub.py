from __future__ import annotations

from pydantic import BaseModel

from app.communication.channels import ChannelAdapter


class OutboundMessage(BaseModel):
    content: str
    reason: str = ""


class CommunicationHub:
    def __init__(self) -> None:
        self.channels: dict[str, ChannelAdapter] = {}
        self.outbox: list[OutboundMessage] = []

    def register_channel(self, adapter: ChannelAdapter) -> None:
        self.channels[adapter.name] = adapter

    async def queue_outbound_message(self, content: str, reason: str = "") -> None:
        self.outbox.append(OutboundMessage(content=content, reason=reason))

    def drain_outbox(self) -> list[OutboundMessage]:
        drained = list(self.outbox)
        self.outbox.clear()
        return drained
