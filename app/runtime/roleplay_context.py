from __future__ import annotations

from pydantic import BaseModel, Field

from app.core.types import JsonValue, utc_now


class RoleplayAgentContextEntry(BaseModel):
    created_at: str = Field(default_factory=lambda: utc_now().isoformat())
    kind: str
    content: str
    metadata: dict[str, JsonValue] = Field(default_factory=dict)

    def render(self) -> str:
        return self.content.strip()


class RoleplayAgentContext(BaseModel):
    context_date: str | None = None
    soul_md: str = ""
    plan_context: str = ""
    entries: list[RoleplayAgentContextEntry] = Field(default_factory=list)
    previous_context_date: str | None = None
    previous_entries: list[RoleplayAgentContextEntry] = Field(default_factory=list)

    def add_entry(
        self,
        *,
        kind: str,
        content: str,
        metadata: dict[str, JsonValue] | None = None,
    ) -> RoleplayAgentContextEntry:
        entry = RoleplayAgentContextEntry(
            kind=kind,
            content=content.strip(),
            metadata=metadata or {},
        )
        self.entries.append(entry)
        return entry

    def add_retrieved_memories(
        self,
        memories: list[str],
        *,
        heading: str = "你想起了一些事情：",
        metadata: dict[str, JsonValue] | None = None,
    ) -> RoleplayAgentContextEntry | None:
        cleaned = [item.strip() for item in memories if item.strip()]
        if not cleaned:
            return None
        bullets = "\n".join(f"- {item}" for item in cleaned)
        return self.add_entry(
            kind="retrieved_memory",
            content=f"{heading}\n{bullets}",
            metadata=metadata,
        )

    def add_execution_record(
        self,
        *,
        scene: str = "",
        result: str = "",
        roleplay: str = "",
        metadata: dict[str, JsonValue] | None = None,
    ) -> RoleplayAgentContextEntry | None:
        lines: list[str] = []
        if roleplay.strip():
            lines.append(f"你说：{roleplay.strip()}")
        if scene.strip():
            lines.append(f"场景：{scene.strip()}")
        if result.strip():
            lines.append(f"结果：{result.strip()}")
        if not lines:
            return None
        return self.add_entry(
            kind="execution_record",
            content="\n".join(lines),
            metadata=metadata,
        )

    def add_interaction_message(
        self,
        *,
        channel_name: str,
        partner_name: str,
        message_text: str,
        include_phone_vibration: bool = False,
        metadata: dict[str, JsonValue] | None = None,
    ) -> RoleplayAgentContextEntry | None:
        channel = channel_name.strip()
        partner = partner_name.strip()
        message = message_text.strip()
        if not partner or not message:
            return None

        lines: list[str] = []
        if include_phone_vibration:
            lines.append("你的手机震动了一下。")
            lines.append("")
        if channel:
            lines.append(f"【{channel}】")
        lines.append(f"{partner}: {message}")
        return self.add_entry(
            kind="interaction_record",
            content="\n".join(lines).strip(),
            metadata=metadata,
        )

    def add_interaction_reply(
        self,
        *,
        roleplay_name: str,
        reply_text: str,
        metadata: dict[str, JsonValue] | None = None,
    ) -> RoleplayAgentContextEntry | None:
        speaker = roleplay_name.strip() or "角色"
        reply = reply_text.strip()
        if not reply:
            return None
        return self.add_entry(
            kind="interaction_record",
            content=f"{speaker}: {reply}",
            metadata=metadata,
        )

    def add_outbound_interaction_message(
        self,
        *,
        channel_name: str,
        partner_name: str,
        roleplay_name: str,
        message_text: str,
        metadata: dict[str, JsonValue] | None = None,
    ) -> RoleplayAgentContextEntry | None:
        channel = channel_name.strip()
        partner = partner_name.strip()
        speaker = roleplay_name.strip() or "角色"
        message = message_text.strip()
        if not partner or not message:
            return None

        lines: list[str] = [f"你打开了和{partner}的聊天窗口。", ""]
        if channel:
            lines.append(f"【{channel}】")
        lines.append(f"{speaker}: {message}")
        return self.add_entry(
            kind="interaction_record",
            content="\n".join(lines).strip(),
            metadata=metadata,
        )

    def render_entries(self, *, limit: int | None = None) -> str:
        selected = self.entries[-limit:] if limit is not None and limit > 0 else self.entries
        chunks = [entry.render() for entry in selected if entry.render()]
        return "\n\n".join(chunks).strip()

    def render_for_roleplay(self, *, entry_limit: int | None = None) -> str:
        blocks = [
            block
            for block in (
                self._render_soul_block(),
                self._render_plan_block(),
                self._render_entries_block(entry_limit=entry_limit),
            )
            if block.strip()
        ]
        return "\n\n".join(blocks).strip()

    def _render_soul_block(self) -> str:
        soul_md = self.soul_md.strip()
        if not soul_md:
            return ""
        return f"soul.md:\n{soul_md}"

    def _render_plan_block(self) -> str:
        plan_context = self.plan_context.strip()
        if not plan_context:
            return ""
        return f"当前计划:\n{plan_context}"

    def _render_entries_block(self, *, entry_limit: int | None) -> str:
        entries_text = self.render_entries(limit=entry_limit)
        if not entries_text:
            return ""
        return entries_text.strip()
