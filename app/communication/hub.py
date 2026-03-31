from __future__ import annotations

from app.communication.channels import OutboundMessage


class CommunicationHub:
    def __init__(self) -> None:
        self.outbox: list[OutboundMessage] = []

    def emit(self, message: OutboundMessage) -> None:
        self.outbox.append(message)

    def drain_outbox(self) -> list[OutboundMessage]:
        drained = list(self.outbox)
        self.outbox.clear()
        return drained
