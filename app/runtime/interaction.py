from __future__ import annotations

from dataclasses import dataclass

from app.communication.channels import OutboundMessage
from app.core.events import EventType, RuntimeEvent
from app.core.outcomes import ActionOutcome, ExecutionTraceEntry, OutcomeStatus
from app.core.state import RuntimeState
from app.core.types import ExecutionMode, ExecutionZone, new_id
from app.runtime.contact_book import ContactBook
from app.runtime.roleplay_agent import RoleplayAgent
from app.runtime.roleplay_context import RoleplayAgentContext


def resolve_interaction_partner(event: RuntimeEvent | None) -> str | None:
    if event is None or event.event_type != EventType.MESSAGE_RECEIVED:
        return None
    for key in ("user_name", "display_name", "user_id"):
        value = str(event.payload.get(key, "")).strip()
        if value:
            return value
    return None


@dataclass(slots=True)
class InteractionExecutionResult:
    outcome: ActionOutcome
    messages: list[OutboundMessage]
    memory_content: str
    interaction_partner: str | None


class InteractionService:
    def __init__(
        self,
        *,
        memory_service: object | None = None,
        roleplay_agent: RoleplayAgent | None = None,
        contact_book: ContactBook | None = None,
    ) -> None:
        self.memory_service = memory_service
        self.roleplay_agent = roleplay_agent
        self.contact_book = contact_book or ContactBook()

    async def execute_interaction(
        self,
        *,
        event: RuntimeEvent,
        state: RuntimeState,
    ) -> InteractionExecutionResult:
        if event.event_type != EventType.MESSAGE_RECEIVED:
            raise RuntimeError("InteractionService 只处理 MESSAGE_RECEIVED 事件。")
        if self.roleplay_agent is None:
            raise RuntimeError("InteractionService 当前没有接入 RoleplayAgent。")

        channel_name = str(event.payload.get("channel", "api")).strip() or "api"
        recipient_id = str(event.payload.get("user_id", "default-user")).strip() or "default-user"
        message_text = str(event.payload.get("text", "")).strip()
        partner_name = resolve_interaction_partner(event) or "对方"
        persona_name = state.persona_name.strip() or "角色"

        self.contact_book.remember_user(
            name=partner_name,
            recipient_id=recipient_id,
            channel=channel_name,
        )

        context = self._build_roleplay_context(state=state)
        await self._inject_interaction_memories(
            context=context,
            roleplay_name=persona_name,
            partner_name=partner_name,
            message_text=message_text,
            channel_name=channel_name,
        )
        context.add_interaction_message(
            channel_name=channel_name,
            partner_name=partner_name,
            message_text=message_text,
            include_phone_vibration=True,
            metadata={
                "source": "interaction",
                "channel": channel_name,
                "interaction_partner": partner_name,
                "direction": "incoming",
                "event_id": event.event_id,
            },
        )
        reply = await self.roleplay_agent.respond_to_interaction(
            context=context,
            state=state,
            event=event,
            channel_name=channel_name,
            partner_name=partner_name,
            message_text=message_text,
        )
        context.add_interaction_reply(
            roleplay_name=persona_name,
            reply_text=reply,
            metadata={
                "source": "interaction",
                "channel": channel_name,
                "interaction_partner": partner_name,
                "direction": "outgoing",
                "event_id": event.event_id,
            },
        )
        self._save_roleplay_context(context)

        message = OutboundMessage(
            channel=channel_name,
            recipient_id=recipient_id,
            content=reply,
        )
        outcome = ActionOutcome(
            action_id=event.event_id,
            status=OutcomeStatus.SUCCESS,
            mode=ExecutionMode.NARRATIVE,
            source=ExecutionZone.NON_REAL,
            content=reply,
            execution_trace=[
                ExecutionTraceEntry(stage="interaction_incoming", content=message_text),
                ExecutionTraceEntry(stage="interaction_reply", content=reply),
            ],
            raw_data={
                "channel": channel_name,
                "recipient_id": recipient_id,
                "interaction_partner": partner_name,
                "incoming_text": message_text,
                "reply_text": reply,
                "roleplay_context": context.render_for_roleplay(),
            },
        )
        return InteractionExecutionResult(
            outcome=outcome,
            messages=[message],
            memory_content=self._render_interaction_memory(
                channel_name=channel_name,
                partner_name=partner_name,
                message_text=message_text,
                roleplay_name=persona_name,
                reply_text=reply,
            ),
            interaction_partner=partner_name,
        )

    async def execute_outbound_interaction(
        self,
        *,
        state: RuntimeState,
        partner_name: str,
        message_text: str,
    ) -> InteractionExecutionResult:
        partner = partner_name.strip()
        outgoing = message_text.strip()
        persona_name = state.persona_name.strip() or "角色"
        if not partner or not outgoing:
            outcome = ActionOutcome(
                action_id=new_id("outbound"),
                status=OutcomeStatus.BLOCKED_FAILURE,
                mode=ExecutionMode.NARRATIVE,
                source=ExecutionZone.NON_REAL,
                content="主动触达缺少目标或消息内容。",
                execution_trace=[],
                raw_data={},
            )
            return InteractionExecutionResult(
                outcome=outcome,
                messages=[],
                memory_content="",
                interaction_partner=partner or None,
            )

        contact = self.contact_book.resolve(partner)
        if contact is None or not contact.enabled:
            summary = f"想要主动联系 {partner}，但花名册中没有这个联系人。"
            outcome = ActionOutcome(
                action_id=new_id("outbound"),
                status=OutcomeStatus.BLOCKED_FAILURE,
                mode=ExecutionMode.NARRATIVE,
                source=ExecutionZone.NON_REAL,
                content=summary,
                execution_trace=[
                    ExecutionTraceEntry(stage="interaction_outgoing_blocked", content=summary),
                ],
                raw_data={"interaction_partner": partner},
            )
            return InteractionExecutionResult(
                outcome=outcome,
                messages=[],
                memory_content=summary,
                interaction_partner=partner,
            )

        context = self._build_roleplay_context(state=state)
        await self._inject_interaction_memories(
            context=context,
            roleplay_name=persona_name,
            partner_name=partner,
            message_text=outgoing,
            channel_name=contact.channel,
        )
        context.add_outbound_interaction_message(
            channel_name=contact.channel,
            partner_name=partner,
            roleplay_name=persona_name,
            message_text=outgoing,
            metadata={
                "source": "interaction",
                "channel": contact.channel,
                "interaction_partner": partner,
                "direction": "outgoing_initiated",
            },
        )
        self._save_roleplay_context(context)

        message = OutboundMessage(
            channel=contact.channel,
            recipient_id=contact.recipient_id,
            content=outgoing,
        )
        outcome = ActionOutcome(
            action_id=new_id("outbound"),
            status=OutcomeStatus.SUCCESS,
            mode=ExecutionMode.NARRATIVE,
            source=ExecutionZone.NON_REAL,
            content=outgoing,
            execution_trace=[
                ExecutionTraceEntry(stage="interaction_outgoing", content=outgoing),
            ],
            raw_data={
                "channel": contact.channel,
                "recipient_id": contact.recipient_id,
                "interaction_partner": partner,
                "outgoing_text": outgoing,
                "roleplay_context": context.render_for_roleplay(),
            },
        )
        return InteractionExecutionResult(
            outcome=outcome,
            messages=[message],
            memory_content=self._render_outbound_interaction_memory(
                channel_name=contact.channel,
                partner_name=partner,
                roleplay_name=persona_name,
                message_text=outgoing,
            ),
            interaction_partner=partner,
        )

    async def _inject_interaction_memories(
        self,
        *,
        context: RoleplayAgentContext,
        roleplay_name: str,
        partner_name: str,
        message_text: str,
        channel_name: str,
    ) -> None:
        injector = getattr(self.memory_service, "retrieve_and_inject_interaction_memories", None)
        if not callable(injector):
            return
        query_text = "\n".join(
            part for part in [partner_name.strip(), message_text.strip()] if part
        ).strip()
        if not query_text:
            return
        await injector(
            query_text=query_text,
            context=context,
            roleplay_name=roleplay_name,
            interaction_partner=partner_name,
            top_k=3,
            metadata={
                "channel": channel_name,
                "trigger": "incoming_message",
            },
        )

    def _build_roleplay_context(self, *, state: RuntimeState) -> RoleplayAgentContext:
        builder = getattr(self.memory_service, "build_roleplay_agent_context", None)
        if callable(builder):
            return builder(state=state)
        return RoleplayAgentContext()

    def _save_roleplay_context(self, context: RoleplayAgentContext) -> None:
        saver = getattr(self.memory_service, "save_roleplay_agent_context", None)
        if callable(saver):
            saver(context)

    def _render_interaction_memory(
        self,
        *,
        channel_name: str,
        partner_name: str,
        message_text: str,
        roleplay_name: str,
        reply_text: str,
    ) -> str:
        return "\n".join(
            [
                "你的手机震动了一下。",
                f"【{channel_name}】",
                f"{partner_name}: {message_text}",
                f"{roleplay_name}: {reply_text}",
            ]
        ).strip()

    def _render_outbound_interaction_memory(
        self,
        *,
        channel_name: str,
        partner_name: str,
        roleplay_name: str,
        message_text: str,
    ) -> str:
        return "\n".join(
            [
                f"你打开了和{partner_name}的聊天窗口。",
                f"【{channel_name}】",
                f"{roleplay_name}: {message_text}",
            ]
        ).strip()


class InteractionPolicy(InteractionService):
    """Compatibility alias while callers migrate to the new interaction-first service."""
