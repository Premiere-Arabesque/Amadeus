from __future__ import annotations

from app.communication.channels import OutboundMessage
from app.core.events import EventType, RuntimeEvent
from app.core.outcomes import ActionOutcome
from app.core.state import RuntimeState
from app.memory.service import MemoryService


def resolve_interaction_partner(event: RuntimeEvent | None) -> str | None:
    if event is None or event.event_type != EventType.MESSAGE_RECEIVED:
        return None
    for key in ("user_name", "display_name", "user_id"):
        value = str(event.payload.get(key, "")).strip()
        if value:
            return value
    return None


class InteractionPolicy:
    def __init__(self, *, memory_service: MemoryService | None = None) -> None:
        self.memory_service = memory_service

    async def build_messages(
        self,
        *,
        event: RuntimeEvent | None,
        outcome: ActionOutcome,
        state: RuntimeState,
    ) -> list[OutboundMessage]:
        del state
        if event is None or event.event_type != EventType.MESSAGE_RECEIVED:
            return []

        channel = str(event.payload.get("channel", "api"))
        recipient_id = str(event.payload.get("user_id", "default-user"))
        text = str(event.payload.get("text", "")).strip()
        partner_name = resolve_interaction_partner(event)
        retrieved_memories = await self._retrieve_memories(
            partner_name=partner_name,
            text=text,
        )
        content = self._render_message(
            text=text,
            outcome=outcome,
            retrieved_memories=retrieved_memories,
        )
        return [
            OutboundMessage(
                channel=channel,
                recipient_id=recipient_id,
                content=content,
            )
        ]

    async def _retrieve_memories(self, *, partner_name: str | None, text: str) -> list[str]:
        if self.memory_service is None or not text:
            return []
        return await self.memory_service.interaction_memory_context(
            partner_name=partner_name,
            query_text=text,
        )

    def _render_message(
        self,
        *,
        text: str,
        outcome: ActionOutcome,
        retrieved_memories: list[str],
    ) -> str:
        result = outcome.raw_data.get("result", {})
        memory_prefix = self._memory_prefix(retrieved_memories)
        if isinstance(result, dict) and outcome.tool_invocations:
            capability = outcome.tool_invocations[0].capability
            if capability == "read_url":
                title = str(result.get("title", "")).strip()
                content = str(result.get("content", "")).strip()
                key_point = content[:180].strip() or outcome.content
                label = title or "对方分享的页面"
                return f"{memory_prefix}我从 {label} 里提炼到的重点是：{key_point}".strip()
            if capability == "search_web":
                abstract_text = str(result.get("abstract_text", "")).strip()
                results = result.get("results", [])
                if abstract_text:
                    return f"{memory_prefix}我找到一条比较有价值的线索：{abstract_text[:220].strip()}".strip()
                if isinstance(results, list) and results:
                    first = results[0]
                    if isinstance(first, dict):
                        return (
                            f"{memory_prefix}我找到一条比较有价值的线索："
                            f"{first.get('text', outcome.content)}"
                        ).strip()

        if text:
            return (
                f"{memory_prefix}我已经根据你的消息调整了接下来几分钟的安排。"
                f"眼下最直接的结论是：{outcome.content}"
            ).strip()
        return f"{memory_prefix}{outcome.content}".strip()

    def _memory_prefix(self, retrieved_memories: list[str]) -> str:
        if not retrieved_memories:
            return ""
        snippet = retrieved_memories[0][:120].strip().rstrip(".")
        if not snippet:
            return ""
        return f"顺着我们之前聊到的“{snippet}”继续说。"
