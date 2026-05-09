from __future__ import annotations

from app.core.events import RuntimeEvent
from app.core.state import PlanStep, RuntimeState
from app.infra.model_client import ModelClient, ModelRouter
from app.infra.settings import ModelRole
from app.runtime.roleplay_context import RoleplayAgentContext


class RoleplayAgent:
    async def respond(
        self,
        *,
        context: RoleplayAgentContext,
        step: PlanStep,
        state: RuntimeState,
        event: RuntimeEvent | None,
        scene: str,
        result: str,
        turn_index: int,
    ) -> str:
        raise NotImplementedError

    async def respond_to_interaction(
        self,
        *,
        context: RoleplayAgentContext,
        state: RuntimeState,
        event: RuntimeEvent,
        channel_name: str,
        partner_name: str,
        message_text: str,
    ) -> str:
        raise NotImplementedError


class ModelRoleplayAgent(RoleplayAgent):
    def __init__(
        self,
        *,
        model_client: ModelClient | None,
        model_router: ModelRouter | None,
    ) -> None:
        self.model_client = model_client
        self.model_router = model_router

    async def respond(
        self,
        *,
        context: RoleplayAgentContext,
        step: PlanStep,
        state: RuntimeState,
        event: RuntimeEvent | None,
        scene: str,
        result: str,
        turn_index: int,
    ) -> str:
        request = self._build_dialogue_request(
            state=state,
            prompt=self._execution_prompt(
                context=context,
                step=step,
                event=event,
                scene=scene,
                result=result,
                turn_index=turn_index,
            ),
        )
        response = await self.model_client.generate_text(request)
        return response.text.strip()

    async def respond_to_interaction(
        self,
        *,
        context: RoleplayAgentContext,
        state: RuntimeState,
        event: RuntimeEvent,
        channel_name: str,
        partner_name: str,
        message_text: str,
    ) -> str:
        request = self._build_dialogue_request(
            state=state,
            prompt=self._interaction_prompt(
                context=context,
                event=event,
                channel_name=channel_name,
                partner_name=partner_name,
                message_text=message_text,
            ),
        )
        response = await self.model_client.generate_text(request)
        return response.text.strip()

    def _build_dialogue_request(self, *, state: RuntimeState, prompt: str):
        if self.model_client is None or self.model_router is None:
            raise RuntimeError("RoleplayAgent 未配置模型客户端。")

        route = self.model_router.resolve(ModelRole.DIALOGUE)
        if not route.is_configured():
            raise RuntimeError("RoleplayAgent 当前没有可用的 dialogue 模型配置。")

        return self.model_router.build_request(
            ModelRole.DIALOGUE,
            prompt=prompt,
            system_prompt=self._system_prompt(state=state),
        )

    def _system_prompt(self, *, state: RuntimeState) -> str:
        persona_name = state.persona_name.strip() or "当前角色"
        return f"""
你现在扮演 {persona_name}。
你只会收到角色自己的上下文、回忆、正在经历的场景和外部消息。
请始终以角色本人的视角，用自然、简短、具体的中文回复。
不要暴露系统、提示词、tool、MCP、schema、JSON。
""".strip()

    def _execution_prompt(
        self,
        *,
        context: RoleplayAgentContext,
        step: PlanStep,
        event: RuntimeEvent | None,
        scene: str,
        result: str,
        turn_index: int,
    ) -> str:
        event_text = str(event.payload.get("text", "")).strip() if event is not None else ""
        blocks = [context.render_for_roleplay()]
        blocks.append(f"当前分钟级动作：{step.title}")
        if step.detail.strip():
            blocks.append(f"动作补充：{step.detail}")
        if event_text:
            blocks.append(f"关联用户消息：{event_text}")
        blocks.append(f"最新场景：\n{scene or '无'}")
        blocks.append(f"最新结果：\n{result or '无'}")
        blocks.append(f"这是你今天这段体验里的第 {turn_index + 1} 次自然反应，请直接说出你此刻会说的话。")
        return "\n\n".join(block for block in blocks if block.strip()).strip()

    def _interaction_prompt(
        self,
        *,
        context: RoleplayAgentContext,
        event: RuntimeEvent,
        channel_name: str,
        partner_name: str,
        message_text: str,
    ) -> str:
        correlation_id = event.correlation_id or ""
        blocks = [context.render_for_roleplay()]
        if correlation_id:
            blocks.append(f"当前交互线程：{correlation_id}")
        if channel_name.strip():
            blocks.append(f"消息渠道：{channel_name.strip()}")
        blocks.append(f"发来消息的人：{partner_name.strip()}")
        blocks.append(f"对方刚刚说：\n{message_text.strip()}")
        blocks.append("你的回复是？")
        return "\n\n".join(block for block in blocks if block.strip()).strip()
