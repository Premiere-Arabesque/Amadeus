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
    soul_md: str = ""
    plan_context: str = ""
    entries: list[RoleplayAgentContextEntry] = Field(default_factory=list)

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
        heading: str = "这几条记忆在你脑海中浮现：",
        metadata: dict[str, JsonValue] | None = None,
    ) -> RoleplayAgentContextEntry | None:
        cleaned = [item.strip() for item in memories if item.strip()]
        if not cleaned:
            return None
        numbered = "\n".join(f"{index}. {item}" for index, item in enumerate(cleaned, start=1))
        return self.add_entry(
            kind="retrieved_memory",
            content=f"{heading}\n{numbered}",
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

    def render_entries(self, *, limit: int | None = None) -> str:
        selected = self.entries[-limit:] if limit is not None and limit > 0 else self.entries
        chunks = [entry.render() for entry in selected if entry.render()]
        return "\n\n".join(chunks).strip()

    def render_for_roleplay(self, *, entry_limit: int | None = None) -> str:
        soul_block = self._render_soul_block()
        plan_block = self._render_plan_block()
        entries_block = self._render_entries_block(entry_limit=entry_limit)
        blocks = [block for block in [soul_block, plan_block, entries_block] if block.strip()]
        return "\n\n".join(blocks).strip()

    # 这里刻意拆成几个小函数，方便后续直接手改每个块的文案模板。
    def _render_soul_block(self) -> str:
        soul_md = self.soul_md.strip()
        if not soul_md:
            return ""
        return f"""soul.md:
{soul_md}""".strip()

    def _render_plan_block(self) -> str:
        plan_context = self.plan_context.strip()
        if not plan_context:
            return ""
        return f"""当前计划:
{plan_context}""".strip()

    def _render_entries_block(self, *, entry_limit: int | None) -> str:
        entries_text = self.render_entries(limit=entry_limit)
        if not entries_text:
            return ""
        return f"""{entries_text}""".strip()
