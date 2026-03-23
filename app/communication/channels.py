from __future__ import annotations

from typing import Protocol


class ChannelAdapter(Protocol):
    name: str

    async def send(self, message: str) -> None:
        """Send an outbound message."""
