from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

from pydantic import BaseModel, Field

from app.core.events import RuntimeEvent
from app.core.outcomes import ActionOutcome, ReplanDecision
from app.core.state import DayPlanBlock, PlanStep, RuntimeState
from app.core.types import JsonValue, utc_now
from app.infra.model_client import (
    ModelClient,
    ModelRouter,
    ModelTracePayload,
    StructuredResponse,
)
from app.infra.settings import MemoryRetrievalSettings, MemoryStorageSettings, ModelRole
from app.infra.storage import DatedJsonlStore, JsonFileStore, JsonlFileStore, SnapshotStore
from app.memory.models import (
    ActiveMemoryEntry,
    ArchiveMemoryEntry,
    CoreMemory,
    RawLogEntry,
    RuntimeSnapshot,
)
from app.memory.retrieval import (
    EmbeddingGenerator,
    MemoryCandidate,
    MemoryReranker,
    MemoryRetrievalPipeline,
)
from app.memory.snapshots import make_snapshot
from app.prompts.store import PromptStore
from app.runtime.roleplay_context import (
    RoleplayAgentContext,
    RoleplayAgentContextEntry,
)


class MemorySummaryDraft(BaseModel):
    content: str


class MemoryRerankCandidatePayload(BaseModel):
    entry_id: str
    created_at: str
    source: str
    interaction_partner: str | None = None
    hit_stages: list[str] = Field(default_factory=list)
    preliminary_score: float
    content: str


class MemoryRerankDraft(BaseModel):
    ranked_entry_ids: list[str] = Field(default_factory=list)


class MemoryService:
    def __init__(
        self,
        *,
        raw_log_path: Path | None = None,
        snapshot_path: Path | None = None,
        active_memory_path: Path | None = None,
        core_memory_path: Path | None = None,
        roleplay_context_path: Path | None = None,
        archive_memory_path: Path | None = None,
        storage_settings: MemoryStorageSettings | None = None,
        retrieval_settings: MemoryRetrievalSettings | None = None,
        model_client: ModelClient | None = None,
        model_router: ModelRouter | None = None,
        prompt_store: PromptStore | None = None,
        semantic_entry_embedder: EmbeddingGenerator | None = None,
        emotional_entry_embedder: EmbeddingGenerator | None = None,
        semantic_query_embedder: EmbeddingGenerator | None = None,
        emotional_query_embedder: EmbeddingGenerator | None = None,
        reranker: MemoryReranker | None = None,
    ) -> None:
        self.model_client = model_client
        self.model_router = model_router
        self.prompt_store = prompt_store or PromptStore()
        self.storage_settings = storage_settings or MemoryStorageSettings()
        self.retrieval_settings = retrieval_settings or MemoryRetrievalSettings()
        self.semantic_entry_embedder = semantic_entry_embedder
        self.emotional_entry_embedder = emotional_entry_embedder
        self.retrieval_pipeline = MemoryRetrievalPipeline(
            settings=self.retrieval_settings,
            semantic_query_embedder=semantic_query_embedder,
            emotional_query_embedder=emotional_query_embedder,
            reranker=reranker or self._rerank_with_model,
        )

        self.raw_store = DatedJsonlStore(raw_log_path or Path("memory/raw_log"))
        self.snapshot_store = SnapshotStore(snapshot_path or Path("memory/snapshots.jsonl"))
        self.active_store = JsonlFileStore(active_memory_path or Path("memory/active_memory.jsonl"))
        self.core_store = JsonFileStore(core_memory_path or Path("memory/core_memory.json"))
        self.roleplay_context_store = JsonFileStore(
            roleplay_context_path or Path("memory/roleplay_context.json")
        )
        self.archive_store = JsonlFileStore(
            archive_memory_path or Path("memory/archive_memory.jsonl")
        )

        self.core_memory = self._load_core_memory()
        self.roleplay_context = self._load_roleplay_context()
        self.raw_entries = self._load_raw_entries()
        self.active_entries = self._load_active_entries()
        self.archive_entries = self._load_archive_entries()
        self._roll_archive_if_needed(reference_time=utc_now())

    def bind_model_runtime(
        self,
        *,
        model_client: ModelClient | None,
        model_router: ModelRouter | None,
    ) -> None:
        self.model_client = model_client
        self.model_router = model_router

    def _load_core_memory(self) -> CoreMemory:
        payload = self.core_store.read()
        if payload is None:
            return CoreMemory()
        return CoreMemory.model_validate(payload)

    def _load_roleplay_context(self) -> RoleplayAgentContext:
        payload = self.roleplay_context_store.read()
        if payload is None:
            return RoleplayAgentContext()
        return RoleplayAgentContext.model_validate(payload)

    def _load_raw_entries(self) -> list[RawLogEntry]:
        return [RawLogEntry.model_validate(item) for item in self.raw_store.read_all()]

    def _load_active_entries(self) -> list[ActiveMemoryEntry]:
        return [ActiveMemoryEntry.model_validate(item) for item in self.active_store.read_all()]

    def _load_archive_entries(self) -> list[ArchiveMemoryEntry]:
        return [ArchiveMemoryEntry.model_validate(item) for item in self.archive_store.read_all()]

    def restore_runtime_state(self) -> RuntimeState | None:
        latest = self.latest_snapshot()
        if latest is None:
            return None
        return RuntimeState.model_validate(latest.state)

    async def save_snapshot(self, state: RuntimeState) -> RuntimeSnapshot:
        snapshot = make_snapshot(state)
        await self.snapshot_store.append(snapshot.model_dump(mode="json"))
        return snapshot

    def latest_snapshot(self) -> RuntimeSnapshot | None:
        payload = self.snapshot_store.latest()
        if payload is None:
            return None
        return RuntimeSnapshot.model_validate(payload)

    def recent_raw_entries(self, limit: int = 10) -> list[RawLogEntry]:
        return self.raw_entries[-limit:] if limit > 0 else []

    def recent_active_entries(self, limit: int = 10) -> list[ActiveMemoryEntry]:
        self._roll_archive_if_needed(reference_time=utc_now())
        active_entries = self._active_window_entries(reference_time=utc_now())
        return active_entries[-limit:] if limit > 0 else []

    def recent_archive_entries(self, limit: int = 10) -> list[ArchiveMemoryEntry]:
        self._roll_archive_if_needed(reference_time=utc_now())
        return self.archive_entries[-limit:] if limit > 0 else []

    def day_start_memory_context(self, *, now: datetime, limit: int = 5) -> list[str]:
        target_date = now.date().isoformat()
        previous_context_date = self.roleplay_context.previous_context_date
        if previous_context_date and previous_context_date < target_date:
            previous_contents = [
                entry.content.strip()
                for entry in self.roleplay_context.previous_entries
                if entry.content.strip()
            ]
            if previous_contents:
                return previous_contents[-limit:]

        self._roll_archive_if_needed(reference_time=now)
        historical_entries = [
            entry
            for entry in [*self._active_window_entries(reference_time=now), *self.archive_entries]
            if (entry_date := _entry_date(entry.created_at)) is not None
            and entry_date < target_date
        ]
        historical_entries.sort(key=lambda entry: entry.created_at, reverse=True)
        memories = [entry.content for entry in historical_entries[:limit]]
        return memories

    def update_persona_context(
        self,
        *,
        soul_md: str | None = None,
    ) -> None:
        if soul_md is not None:
            self.core_memory.soul_md = soul_md
        self._touch_core_memory()

    def reset_core_memory(
        self,
        *,
        soul_md: str = "",
    ) -> CoreMemory:
        self.core_memory = CoreMemory(
            soul_md=soul_md,
        )
        self.core_store.write(self.core_memory.model_dump(mode="json"))
        return self.core_memory

    def get_persisted_roleplay_agent_context(self) -> RoleplayAgentContext:
        return self.roleplay_context.model_copy(deep=True)

    def save_roleplay_agent_context(self, context: RoleplayAgentContext) -> RoleplayAgentContext:
        self.roleplay_context = context.model_copy(deep=True)
        self.roleplay_context_store.write(self.roleplay_context.model_dump(mode="json"))
        return self.get_persisted_roleplay_agent_context()

    def reset_roleplay_agent_context(
        self,
        *,
        soul_md: str = "",
        plan_context: str = "",
    ) -> RoleplayAgentContext:
        self.roleplay_context = RoleplayAgentContext(
            soul_md=soul_md.strip(),
            plan_context=plan_context.strip(),
        )
        self.roleplay_context_store.write(self.roleplay_context.model_dump(mode="json"))
        return self.get_persisted_roleplay_agent_context()

    def rotate_roleplay_agent_context_for_day(self, *, target_date: str) -> RoleplayAgentContext:
        cleaned_date = target_date.strip()
        if not cleaned_date:
            return self.get_persisted_roleplay_agent_context()

        current = self.roleplay_context.model_copy(deep=True)
        if current.context_date == cleaned_date:
            return self.get_persisted_roleplay_agent_context()

        if current.context_date:
            current.previous_context_date = current.context_date
            current.previous_entries = [entry.model_copy(deep=True) for entry in current.entries]

        current.context_date = cleaned_date
        current.plan_context = ""
        current.entries = []
        return self.save_roleplay_agent_context(current)

    def update_plan_context(
        self,
        *,
        day_blocks: list[DayPlanBlock],
        plan_date: str | None = None,
    ) -> None:
        del day_blocks, plan_date

    def set_manual_context_memories(
        self,
        memories: list[str],
        *,
        source: str = "manual_context",
    ) -> None:
        cleaned_memories = [str(item).strip() for item in memories if str(item).strip()]
        retained_entries = [entry for entry in self.active_entries if entry.source != source]
        manual_entries = [ActiveMemoryEntry(content=memory, source=source) for memory in cleaned_memories]
        for entry in manual_entries:
            self._populate_memory_embeddings(entry)
        self.active_entries = [*retained_entries, *manual_entries]
        self.active_store.replace_all(
            [entry.model_dump(mode="json") for entry in self.active_entries]
        )

    def record_runtime_event(self, event: RuntimeEvent) -> RawLogEntry:
        entry = RawLogEntry(
            kind="event",
            source=event.source.value,
            payload=event.model_dump(mode="json"),
        )
        self._append_raw(entry)
        return entry

    def record_planning_trace(
        self,
        *,
        plan_scope: str,
        strategy: str,
        trigger_event: RuntimeEvent | None,
        plan_state: BaseModel | None = None,
        prompt: str | None = None,
        system_prompt: str | None = None,
        structured_output: dict[str, JsonValue] | None = None,
        error: str | None = None,
        metadata: dict[str, JsonValue] | None = None,
    ) -> RawLogEntry:
        entry = RawLogEntry(
            kind="planning",
            source="runtime",
            payload={
                "plan_scope": plan_scope,
                "strategy": strategy,
                "trigger_event": (
                    trigger_event.model_dump(mode="json") if trigger_event is not None else None
                ),
                "prompt": prompt,
                "system_prompt": system_prompt,
                "structured_output": structured_output,
                "plan_state": (
                    plan_state.model_dump(mode="json") if plan_state is not None else None
                ),
                "error": error,
                "metadata": metadata or {},
            },
        )
        self._append_raw(entry)
        return entry

    def record_model_trace(self, trace: ModelTracePayload) -> RawLogEntry:
        entry = RawLogEntry(
            created_at=trace.recorded_at,
            kind="model_io",
            source=trace.provider,
            payload=trace.model_dump(mode="json"),
        )
        self._append_raw(entry)
        return entry

    def record_outcome(
        self,
        step: PlanStep,
        outcome: ActionOutcome,
        *,
        memory_content: str | None = None,
        interaction_partner: str | None = None,
    ) -> None:
        raw_entry = RawLogEntry(
            kind="outcome",
            source=outcome.zone.value,
            payload={
                "step": step.model_dump(mode="json"),
                "outcome": outcome.model_dump(mode="json"),
            },
        )
        self._append_raw(raw_entry)

        active_entry = ActiveMemoryEntry(
            content=memory_content or f"{step.title}: {outcome.summary}",
            source=outcome.zone.value,
            interaction_partner=interaction_partner,
        )
        self._populate_memory_embeddings(active_entry)
        self.active_entries.append(active_entry)
        self.active_store.append(active_entry.model_dump(mode="json"))
        reference_time = _parse_entry_datetime(active_entry.created_at) or utc_now()
        self._roll_archive_if_needed(reference_time=reference_time)

    def record_interaction(
        self,
        outcome: ActionOutcome,
        *,
        memory_content: str,
        interaction_partner: str | None = None,
    ) -> None:
        raw_entry = RawLogEntry(
            kind="interaction",
            source="interaction",
            payload={
                "outcome": outcome.model_dump(mode="json"),
                "interaction_partner": interaction_partner,
                "memory_content": memory_content,
            },
        )
        self._append_raw(raw_entry)

        active_entry = ActiveMemoryEntry(
            content=memory_content.strip() or outcome.summary,
            source="interaction",
            interaction_partner=interaction_partner,
        )
        self._populate_memory_embeddings(active_entry)
        self.active_entries.append(active_entry)
        self.active_store.append(active_entry.model_dump(mode="json"))
        reference_time = _parse_entry_datetime(active_entry.created_at) or utc_now()
        self._roll_archive_if_needed(reference_time=reference_time)

    def record_replan_decision(
        self,
        decision: ReplanDecision,
        *,
        event: RuntimeEvent | None,
        outcome: ActionOutcome,
    ) -> RawLogEntry:
        entry = RawLogEntry(
            kind="replan",
            source="runtime",
            payload={
                "decision": decision.model_dump(mode="json"),
                "event_type": event.event_type.value if event is not None else "none",
                "outcome_summary": outcome.summary,
            },
        )
        self._append_raw(entry)
        return entry

    async def interaction_memory_context(
        self,
        *,
        partner_name: str | None,
        query_text: str,
        top_k: int = 3,
    ) -> list[str]:
        reference_time = utc_now()
        self._roll_archive_if_needed(reference_time=reference_time)
        entries = self._active_window_entries(reference_time=reference_time)
        if partner_name:
            partner_entries = [
                entry for entry in entries if entry.interaction_partner == partner_name
            ]
            ranked_partner_entries = await self._rank_entries(
                query_text,
                partner_entries,
                top_k=top_k,
            )
            if ranked_partner_entries:
                return [entry.content for entry in ranked_partner_entries]

            recent_partner_entries = sorted(
                partner_entries,
                key=lambda entry: entry.created_at,
                reverse=True,
            )
            if recent_partner_entries:
                return [entry.content for entry in recent_partner_entries[:top_k]]

        ranked_entries = await self._rank_entries(query_text, entries, top_k=top_k)
        return [entry.content for entry in ranked_entries]

    async def replan_memory_context(
        self,
        *,
        query_text: str,
        top_k: int = 3,
    ) -> list[str]:
        reference_time = utc_now()
        self._roll_archive_if_needed(reference_time=reference_time)
        entries = [
            *self._active_window_entries(reference_time=reference_time),
            *self.archive_entries,
        ]
        ranked_entries = await self._rank_entries(query_text, entries, top_k=top_k)
        if ranked_entries:
            return [entry.content for entry in ranked_entries]

        recent_entries = sorted(entries, key=lambda entry: entry.created_at, reverse=True)
        if recent_entries:
            return [entry.content for entry in recent_entries[:top_k]]

        return []

    async def retrieve_memories(
        self,
        *,
        query_text: str,
        top_k: int = 3,
        interaction_partner: str | None = None,
        include_archive: bool = True,
        reranker_enabled: bool | None = None,
    ) -> list[str]:
        cleaned_query = query_text.strip()
        if not cleaned_query or top_k <= 0:
            return []

        reference_time = utc_now()
        self._roll_archive_if_needed(reference_time=reference_time)
        entries = self._memory_retrieval_entries(
            reference_time=reference_time,
            interaction_partner=interaction_partner,
            include_archive=include_archive,
        )
        ranked_entries = await self._rank_entries_for_context_injection(
            cleaned_query,
            entries,
            top_k=top_k,
            reranker_enabled=reranker_enabled,
        )
        if not ranked_entries:
            return []
        return self._dedupe_memory_contents(
            [entry.content for entry in ranked_entries],
            top_k=top_k,
        )

    async def retrieve_and_inject_memories(
        self,
        *,
        query_text: str,
        context: RoleplayAgentContext,
        roleplay_name: str,
        top_k: int = 3,
        interaction_partner: str | None = None,
        include_archive: bool = True,
        reranker_enabled: bool | None = None,
        source: str,
        metadata: dict[str, JsonValue] | None = None,
    ) -> RoleplayAgentContextEntry | None:
        cleaned_query = query_text.strip()
        memories = await self.retrieve_memories(
            query_text=cleaned_query,
            top_k=top_k,
            interaction_partner=interaction_partner,
            include_archive=include_archive,
            reranker_enabled=reranker_enabled,
        )
        if not memories:
            return None

        entry_metadata: dict[str, JsonValue] = {
            "query_text": cleaned_query,
            "source": source,
            "top_k": top_k,
            "retrieved_count": len(memories),
        }
        if interaction_partner:
            entry_metadata["interaction_partner"] = interaction_partner
        if metadata:
            entry_metadata.update(metadata)

        return context.add_retrieved_memories(
            memories,
            heading="你想起了一些事情：",
            metadata=entry_metadata,
        )

    async def retrieve_and_inject_interaction_memories(
        self,
        *,
        query_text: str,
        context: RoleplayAgentContext,
        roleplay_name: str,
        interaction_partner: str,
        top_k: int = 3,
        reranker_enabled: bool | None = None,
        metadata: dict[str, JsonValue] | None = None,
    ) -> RoleplayAgentContextEntry | None:
        partner_name = interaction_partner.strip()
        cleaned_query = query_text.strip()
        if not partner_name or not cleaned_query:
            return None

        partner_memories = await self.retrieve_memories(
            query_text=cleaned_query,
            top_k=top_k,
            interaction_partner=partner_name,
            include_archive=True,
            reranker_enabled=reranker_enabled,
        )

        retrieval_scope = "partner_only"
        memories = partner_memories
        if not memories:
            retrieval_scope = "global_fallback"
            memories = await self.retrieve_memories(
                query_text=cleaned_query,
                top_k=top_k,
                interaction_partner=None,
                include_archive=True,
                reranker_enabled=reranker_enabled,
            )
        if not memories:
            return None

        entry_metadata: dict[str, JsonValue] = {
            "query_text": cleaned_query,
            "source": "interaction",
            "top_k": top_k,
            "retrieved_count": len(memories),
            "interaction_partner": partner_name,
            "retrieval_scope": retrieval_scope,
        }
        if metadata:
            entry_metadata.update(metadata)

        return context.add_retrieved_memories(
            memories,
            heading="你想起了一些事情：",
            metadata=entry_metadata,
        )

    async def summarize_outcome(
        self,
        step: PlanStep,
        outcome: ActionOutcome,
        *,
        state: RuntimeState,
        event: RuntimeEvent | None = None,
    ) -> str:
        dialogue_memory = self._render_execution_dialogue_memory(step, outcome)
        if dialogue_memory is not None:
            return dialogue_memory

        default_summary = f"{step.title}: {outcome.summary}"
        if self.model_client is None or self.model_router is None:
            return default_summary

        route = self.model_router.resolve(ModelRole.MEMORY)
        if not route.is_configured():
            return default_summary

        event_text = ""
        if event is not None:
            event_text = str(event.payload.get("text", "")).strip()
        summarize_suffix = self._prompt_text(
            "memory/summarize_outcome_user_suffix.txt",
            default="请把这些内容压缩成一条在未来检索中仍然有价值的记忆。",
        )
        request = self.model_router.build_request(
            ModelRole.MEMORY,
            prompt=(
                f"{self.core_prompt_context(state=state, execution_limit=4)}"
                f"步骤标题：{step.title}\n"
                f"步骤细节：{step.detail}\n"
                f"结果摘要：{outcome.summary}\n"
                f"关联用户消息：{event_text or '无'}\n\n"
                f"{summarize_suffix}"
            ),
            system_prompt=self._prompt_text(
                "memory/summarize_outcome_system.txt",
                default=(
                    "你要为人格驱动的运行时提炼简洁的记忆条目。"
                    "只保留耐久、值得被后续检索的信息。"
                ),
            ),
        )
        try:
            response: StructuredResponse[MemorySummaryDraft] = (
                await self.model_client.generate_structured(
                    request,
                    MemorySummaryDraft,
                )
            )
        except Exception:
            return default_summary
        return response.structured.content or default_summary

    def _render_execution_dialogue_memory(
        self,
        step: PlanStep,
        outcome: ActionOutcome,
    ) -> str | None:
        if not outcome.execution_trace:
            return None

        lines = [f"执行动作：{step.title}"]
        if step.detail.strip():
            lines.append(f"动作补充：{step.detail}")

        initial_roleplay = self._trace_content(outcome, "roleplay_initial")
        if initial_roleplay:
            lines.append(f"Roleplay：{initial_roleplay}")

        initial_scene = self._trace_content(
            outcome,
            "scene",
            "tool_scene",
            "fallback_scene",
        )
        initial_result = self._trace_content(
            outcome,
            "result",
            "tool_result",
            "fallback_result",
        )
        if initial_scene or initial_result:
            lines.append("Executor：")
            if initial_scene:
                lines.append(f"场景：{initial_scene}")
            if initial_result:
                lines.append(f"结果：{initial_result}")

        turn_index = 1
        while True:
            roleplay = self._trace_content(outcome, f"agent_response_{turn_index}")
            next_scene = self._trace_content(outcome, f"loop_scene_{turn_index}")
            next_result = self._trace_content(outcome, f"loop_result_{turn_index}")
            if not roleplay and not next_scene and not next_result:
                break
            if roleplay:
                lines.append(f"Roleplay：{roleplay}")
            if next_scene or next_result:
                lines.append("Executor：")
                if next_scene:
                    lines.append(f"场景：{next_scene}")
                if next_result:
                    lines.append(f"结果：{next_result}")
            turn_index += 1

        content = "\n".join(line for line in lines if line.strip()).strip()
        return content or None

    def _execution_dialogue_memory_content(
        self,
        step: PlanStep,
        outcome: ActionOutcome,
    ) -> str | None:
        if not outcome.execution_trace:
            return None

        lines = [f"执行动作：{step.title}"]
        if step.detail.strip():
            lines.append(f"动作补充：{step.detail}")

        initial_scene = self._trace_content(
            outcome,
            "scene",
            "tool_scene",
            "fallback_scene",
        )
        initial_result = self._trace_content(
            outcome,
            "result",
            "tool_result",
            "fallback_result",
        )
        if initial_scene or initial_result:
            lines.append("Executor：")
            if initial_scene:
                lines.append(f"场景：{initial_scene}")
            if initial_result:
                lines.append(f"结果：{initial_result}")

        turn_index = 1
        while True:
            roleplay = self._trace_content(outcome, f"agent_response_{turn_index}")
            next_scene = self._trace_content(outcome, f"loop_scene_{turn_index}")
            next_result = self._trace_content(outcome, f"loop_result_{turn_index}")
            if not roleplay and not next_scene and not next_result:
                break
            if roleplay:
                lines.append(f"Roleplay：{roleplay}")
            if next_scene or next_result:
                lines.append("Executor：")
                if next_scene:
                    lines.append(f"场景：{next_scene}")
                if next_result:
                    lines.append(f"结果：{next_result}")
            turn_index += 1

        content = "\n".join(line for line in lines if line.strip()).strip()
        return content or None

    def _trace_content(self, outcome: ActionOutcome, *stages: str) -> str:
        stage_set = set(stages)
        for entry in outcome.execution_trace:
            if entry.stage in stage_set:
                return entry.content.strip()
        return ""

    async def search_memory(
        self,
        *,
        query_text: str,
        top_k: int = 5,
    ) -> tuple[list[ActiveMemoryEntry], list[ArchiveMemoryEntry]]:
        reference_time = utc_now()
        self._roll_archive_if_needed(reference_time=reference_time)
        active_hits = await self._rank_entries(
            query_text,
            self._active_window_entries(reference_time=reference_time),
            top_k=top_k,
        )
        archive_hits = await self._rank_entries(
            query_text,
            self.archive_entries,
            top_k=top_k,
        )
        return (
            [entry for entry in active_hits if isinstance(entry, ActiveMemoryEntry)],
            [entry for entry in archive_hits if isinstance(entry, ArchiveMemoryEntry)],
        )

    async def search_memory_debug(
        self,
        *,
        query_text: str,
        top_k: int = 5,
    ) -> dict[str, object]:
        reference_time = utc_now()
        self._roll_archive_if_needed(reference_time=reference_time)
        active_entries = self._active_window_entries(reference_time=reference_time)
        archive_entries = self.archive_entries
        return {
            "query": query_text,
            "active": await self.retrieval_pipeline.debug_rank(
                query_text,
                active_entries,
                top_k=top_k,
            ),
            "archive": await self.retrieval_pipeline.debug_rank(
                query_text,
                archive_entries,
                top_k=top_k,
            ),
        }

    async def _rank_entries(
        self,
        query_text: str,
        entries: list[ActiveMemoryEntry | ArchiveMemoryEntry],
        *,
        top_k: int,
    ) -> list[ActiveMemoryEntry | ArchiveMemoryEntry]:
        return await self.retrieval_pipeline.rank(query_text, entries, top_k=top_k)

    async def _rank_entries_for_context_injection(
        self,
        query_text: str,
        entries: list[ActiveMemoryEntry | ArchiveMemoryEntry],
        *,
        top_k: int,
        reranker_enabled: bool | None,
    ) -> list[ActiveMemoryEntry | ArchiveMemoryEntry]:
        settings = self.retrieval_settings.model_copy(
            update={
                "semantic_enabled": True,
                "bm25_enabled": True,
                "emotional_enabled": False,
                "reranker_enabled": (
                    self.retrieval_settings.reranker_enabled
                    if reranker_enabled is None
                    else reranker_enabled
                ),
            }
        )
        pipeline = MemoryRetrievalPipeline(
            settings=settings,
            semantic_query_embedder=self.retrieval_pipeline.semantic_query_embedder,
            emotional_query_embedder=None,
            reranker=self.retrieval_pipeline.reranker,
        )
        return await pipeline.rank(query_text, entries, top_k=top_k)

    def _append_raw(self, entry: RawLogEntry) -> None:
        self.raw_entries.append(entry)
        self.raw_store.append(entry.model_dump(mode="json"))

    def _memory_retrieval_entries(
        self,
        *,
        reference_time: datetime,
        interaction_partner: str | None,
        include_archive: bool,
    ) -> list[ActiveMemoryEntry | ArchiveMemoryEntry]:
        active_entries = self._active_window_entries(reference_time=reference_time)
        if interaction_partner:
            active_entries = [
                entry
                for entry in active_entries
                if entry.interaction_partner == interaction_partner
            ]
        if not include_archive:
            return active_entries

        archive_entries: list[ArchiveMemoryEntry] = list(self.archive_entries)
        if interaction_partner:
            archive_entries = [
                entry
                for entry in archive_entries
                if entry.interaction_partner == interaction_partner
            ]
        return [*active_entries, *archive_entries]

    def _dedupe_memory_contents(
        self,
        contents: list[str],
        *,
        top_k: int,
    ) -> list[str]:
        unique: list[str] = []
        seen: set[str] = set()
        for item in contents:
            cleaned = item.strip()
            if not cleaned:
                continue
            normalized = " ".join(cleaned.split())
            if normalized in seen:
                continue
            seen.add(normalized)
            unique.append(cleaned)
            if len(unique) >= top_k:
                break
        return unique


    def _touch_core_memory(self) -> None:
        self.core_memory.updated_at = utc_now().isoformat()
        self.core_store.write(self.core_memory.model_dump(mode="json"))

    def _touch_roleplay_context(self) -> None:
        self.roleplay_context_store.write(self.roleplay_context.model_dump(mode="json"))

    def _format_plan_lines(self, day_blocks: list[DayPlanBlock]) -> list[str]:
        return [
            f"{block.time}: {block.label}"
            for block in day_blocks
            if block.time.strip() and block.label.strip()
        ]

    # Core memory now only carries stable persona-level information.
    # Keep the rendering logic centralized here so future long-term fields
    # can be added without changing the callers in planning/execution/replan.
    def _core_prompt_sections(self) -> list[str]:
        sections: list[str] = []
        soul_md = self.core_memory.soul_md.strip()
        if soul_md:
            sections.append(f"soul.md:\n{soul_md}")

        stable_facts = [item.strip() for item in self.core_memory.stable_facts if item.strip()]
        if stable_facts:
            sections.append("长期稳定事实:\n" + "\n".join(f"- {item}" for item in stable_facts))

        relationships = [
            item.strip() for item in self.core_memory.relationship_conclusions if item.strip()
        ]
        if relationships:
            sections.append("长期关系结论:\n" + "\n".join(f"- {item}" for item in relationships))

        important_conclusions = [
            item.strip() for item in self.core_memory.important_conclusions if item.strip()
        ]
        if important_conclusions:
            sections.append(
                "长期重要结论:\n" + "\n".join(f"- {item}" for item in important_conclusions)
            )
        return sections

    def core_prompt_context(
        self,
        *,
        state: RuntimeState | None,
        execution_limit: int | None = None,
    ) -> str:
        del state, execution_limit
        sections = self._core_prompt_sections()
        return "\n\n".join(section for section in sections if section.strip()).strip()

    def build_roleplay_agent_context(
        self,
        *,
        state: RuntimeState | None,
        entry_limit: int | None = 12,
    ) -> RoleplayAgentContext:
        context = self.get_persisted_roleplay_agent_context()
        if state is not None and state.plan.plan_date:
            context.context_date = state.plan.plan_date
        context.soul_md = self.core_memory.soul_md.strip()
        today_plan_lines = self._format_plan_lines(state.plan.day_blocks) if state is not None else []
        context.plan_context = "\n".join(
            line for line in today_plan_lines if line.strip()
        ).strip()
        if entry_limit is not None and entry_limit > 0 and len(context.entries) > entry_limit:
            context.entries = context.entries[-entry_limit:]
        return context

    def _active_window_entries(
        self,
        *,
        reference_time: datetime,
    ) -> list[ActiveMemoryEntry]:
        cutoff = reference_time - timedelta(days=self.storage_settings.active_retention_days)
        active_entries: list[ActiveMemoryEntry] = []
        for entry in self.active_entries:
            created_at = _parse_entry_datetime(entry.created_at)
            if created_at is None or created_at >= cutoff:
                active_entries.append(entry)
        return active_entries

    def _roll_archive_if_needed(self, *, reference_time: datetime) -> None:
        cutoff = reference_time - timedelta(days=self.storage_settings.active_retention_days)
        stale_entries: list[ActiveMemoryEntry] = []
        retained_entries: list[ActiveMemoryEntry] = []
        for entry in self.active_entries:
            created_at = _parse_entry_datetime(entry.created_at)
            if created_at is not None and created_at < cutoff:
                stale_entries.append(entry)
                continue
            retained_entries.append(entry)

        if not stale_entries:
            return
        self.active_entries = retained_entries
        self.active_store.replace_all(
            [entry.model_dump(mode="json") for entry in self.active_entries]
        )

        archived = [
            ArchiveMemoryEntry(
                created_at=entry.created_at,
                content=entry.content,
                source=entry.source,
                interaction_partner=entry.interaction_partner,
            )
            for entry in stale_entries
        ]
        self.archive_entries.extend(archived)
        for entry in archived:
            self.archive_store.append(entry.model_dump(mode="json"))

    def _populate_memory_embeddings(self, entry: ActiveMemoryEntry) -> None:
        semantic_embedding = self._safe_generate_embedding(
            self.semantic_entry_embedder,
            entry.content,
        )
        if semantic_embedding:
            entry.semantic_embedding = semantic_embedding
        emotional_embedding = self._safe_generate_embedding(
            self.emotional_entry_embedder,
            entry.content,
        )
        if emotional_embedding:
            entry.emotional_embedding = emotional_embedding

    def _safe_generate_embedding(
        self,
        generator: EmbeddingGenerator | None,
        text: str,
    ) -> list[float] | None:
        if generator is None:
            return None
        try:
            return generator(text)
        except Exception:
            return None

    async def _rerank_with_model(
        self,
        query_text: str,
        candidates: list[MemoryCandidate],
        top_k: int,
    ) -> list[ActiveMemoryEntry | ArchiveMemoryEntry] | None:
        if self.model_client is None or self.model_router is None:
            return None
        if len(candidates) <= 1:
            return [candidate.entry for candidate in candidates[:top_k]]

        route = self.model_router.resolve(ModelRole.MEMORY)
        if not route.is_configured():
            return None

        candidate_payload = [
            MemoryRerankCandidatePayload(
                entry_id=candidate.entry.entry_id,
                created_at=candidate.entry.created_at,
                source=candidate.entry.source,
                interaction_partner=candidate.entry.interaction_partner,
                hit_stages=list(candidate.hit_stages),
                preliminary_score=round(candidate.score, 6),
                content=candidate.entry.content,
            ).model_dump(mode="json")
            for candidate in candidates
        ]
        rerank_suffix = self._prompt_text(
            "memory/rerank_user_suffix.txt",
            default=(
                "下面是从语义检索、BM25 和其他检索阶段汇总并去重后的候选记忆条目。"
                "请按它们与当前场景或查询的相关性重新排序，只返回排好序的 entry id。"
            ),
        )
        request = self.model_router.build_request(
            ModelRole.MEMORY,
            prompt=(
                f"当前场景/查询：\n{query_text}\n\n"
                f"请求返回的 top_n：{top_k}\n"
                f"{rerank_suffix}\n\n"
                f"候选条目：\n{json.dumps(candidate_payload, ensure_ascii=False, indent=2)}"
            ),
            system_prompt=self._prompt_text(
                "memory/rerank_system.txt",
                default=(
                    "你要为人格驱动的运行时重排候选记忆条目。"
                    "相比单纯的新近性，更优先考虑具体主题相关性和连续性价值。"
                    "只返回结构化输出。"
                ),
            ),
        )
        try:
            response: StructuredResponse[MemoryRerankDraft] = (
                await self.model_client.generate_structured(
                    request,
                    MemoryRerankDraft,
                )
            )
        except Exception:
            return None

        by_id = {candidate.entry.entry_id: candidate.entry for candidate in candidates}
        ranked_entries: list[ActiveMemoryEntry | ArchiveMemoryEntry] = []
        seen: set[str] = set()
        for entry_id in response.structured.ranked_entry_ids:
            entry = by_id.get(entry_id)
            if entry is None or entry_id in seen:
                continue
            seen.add(entry_id)
            ranked_entries.append(entry)
            if len(ranked_entries) >= top_k:
                break
        return ranked_entries or None

    def _prompt_text(self, path: str, *, default: str) -> str:
        return self.prompt_store.load(path, default=default).strip()


def _entry_date(value: str) -> str | None:
    try:
        return datetime.fromisoformat(value).date().isoformat()
    except Exception:
        return None


def _parse_entry_datetime(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value)
    except Exception:
        return None

