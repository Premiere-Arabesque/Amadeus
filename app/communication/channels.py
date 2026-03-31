from __future__ import annotations

from pydantic import BaseModel, Field

from app.core.types import new_id, utc_now


class OutboundMessage(BaseModel):
    message_id: str = Field(default_factory=lambda: new_id("msg"))
    channel: str = "api"
    recipient_id: str = "default-user"
    content: str
    created_at: str = Field(default_factory=lambda: utc_now().isoformat())
