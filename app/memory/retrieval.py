from __future__ import annotations

import inspect
import math
import re
from collections import Counter
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from app.infra.settings import MemoryRetrievalSettings
from app.memory.models import ActiveMemoryEntry, ArchiveMemoryEntry

MemoryEntry = ActiveMemoryEntry | ArchiveMemoryEntry
QueryEmbedder = Callable[[str], list[float] | None]
EmbeddingGenerator = QueryEmbedder
MemoryReranker = Callable[
    [str, list["MemoryCandidate"], int],
    Awaitable[list[MemoryEntry] | None] | list[MemoryEntry] | None,
]


@dataclass(slots=True)
class ScoredMemoryEntry:
    entry: MemoryEntry
    score: float
    stage: str


@dataclass(slots=True)
class MemoryCandidate:
    entry: MemoryEntry
    score: float
    hit_stages: tuple[str, ...]


async def rank_memory_entries(
    query_text: str,
    entries: list[MemoryEntry],
    *,
    top_k: int,
) -> list[MemoryEntry]:
    settings = MemoryRetrievalSettings()
    return await MemoryRetrievalPipeline(settings=settings).rank(
        query_text,
        entries,
        top_k=top_k,
    )


class MemoryRetrievalPipeline:
    def __init__(
        self,
        *,
        settings: MemoryRetrievalSettings,
        semantic_query_embedder: QueryEmbedder | None = None,
        emotional_query_embedder: QueryEmbedder | None = None,
        reranker: MemoryReranker | None = None,
    ) -> None:
        self.settings = settings
        self.semantic_query_embedder = semantic_query_embedder
        self.emotional_query_embedder = emotional_query_embedder
        self.reranker = reranker

    async def rank(
        self,
        query_text: str,
        entries: list[MemoryEntry],
        *,
        top_k: int,
    ) -> list[MemoryEntry]:
        if top_k <= 0:
            return []
        query_text = query_text.strip()
        if not query_text:
            return []

        stage_hits = [
            *self._semantic_hits(query_text, entries),
            *self._bm25_hits(query_text, entries),
            *self._emotional_hits(query_text, entries),
        ]
        if not stage_hits:
            return []

        combined = self._combine_stage_hits(stage_hits)
        candidates = [
            candidate
            for candidate in sorted(
                combined.values(),
                key=lambda item: (item.score, item.entry.created_at),
                reverse=True,
            )
        ][: max(top_k, self.settings.candidate_pool_size)]

        reranked = await self._rerank(query_text, candidates, top_k=top_k)
        if reranked is not None:
            return reranked[:top_k]
        return [candidate.entry for candidate in candidates[:top_k]]

    async def debug_rank(
        self,
        query_text: str,
        entries: list[MemoryEntry],
        *,
        top_k: int,
    ) -> dict[str, Any]:
        query_text = query_text.strip()
        stage_hits_by_name = {
            "semantic": self._semantic_hits(query_text, entries)
            if query_text and top_k > 0
            else [],
            "bm25": self._bm25_hits(query_text, entries)
            if query_text and top_k > 0
            else [],
            "emotional": self._emotional_hits(query_text, entries)
            if query_text and top_k > 0
            else [],
        }
        combined = self._combine_stage_hits(
            [
                *stage_hits_by_name["semantic"],
                *stage_hits_by_name["bm25"],
                *stage_hits_by_name["emotional"],
            ]
        )
        candidates = [
            candidate
            for candidate in sorted(
                combined.values(),
                key=lambda item: (item.score, item.entry.created_at),
                reverse=True,
            )
        ][: max(top_k, self.settings.candidate_pool_size)] if query_text and top_k > 0 else []
        reranked = await self._rerank(query_text, candidates, top_k=top_k) if candidates else None
        final_entries = (
            reranked[:top_k]
            if reranked is not None
            else [candidate.entry for candidate in candidates[:top_k]]
        )
        return {
            "settings": {
                "semantic_enabled": self.settings.semantic_enabled,
                "bm25_enabled": self.settings.bm25_enabled,
                "emotional_enabled": self.settings.emotional_enabled,
                "reranker_enabled": self.settings.reranker_enabled,
                "candidate_pool_size": self.settings.candidate_pool_size,
            },
            "stage_hits": {
                stage: [_scored_hit_debug(hit) for hit in hits]
                for stage, hits in stage_hits_by_name.items()
            },
            "combined_candidates": [_candidate_debug(candidate) for candidate in candidates],
            "reranked_entry_ids": (
                [entry.entry_id for entry in reranked]
                if reranked is not None
                else []
            ),
            "final_entries": [_entry_debug(entry) for entry in final_entries],
        }

    def _semantic_hits(
        self,
        query_text: str,
        entries: list[MemoryEntry],
    ) -> list[ScoredMemoryEntry]:
        if not self.settings.semantic_enabled or self.semantic_query_embedder is None:
            return []
        query_embedding = self._safe_embed(self.semantic_query_embedder, query_text)
        if not query_embedding:
            return []
        hits: list[ScoredMemoryEntry] = []
        for entry in entries:
            if not entry.semantic_embedding:
                continue
            score = _cosine_similarity(query_embedding, entry.semantic_embedding)
            if score > 0:
                hits.append(ScoredMemoryEntry(entry=entry, score=score, stage="semantic"))
        return hits

    def _bm25_hits(
        self,
        query_text: str,
        entries: list[MemoryEntry],
    ) -> list[ScoredMemoryEntry]:
        if not self.settings.bm25_enabled:
            return []
        query_tokens = _tokenize(query_text)
        if not query_tokens or not entries:
            return []

        doc_tokens = [_tokenize(entry.content) for entry in entries]
        if not any(doc_tokens):
            return []

        document_frequencies = Counter[str]()
        for tokens in doc_tokens:
            for token in set(tokens):
                document_frequencies[token] += 1

        avg_doc_len = sum(len(tokens) for tokens in doc_tokens) / max(len(doc_tokens), 1)
        hits: list[ScoredMemoryEntry] = []
        for entry, tokens in zip(entries, doc_tokens, strict=False):
            if not tokens:
                continue
            term_counts = Counter(tokens)
            score = 0.0
            for token in query_tokens:
                frequency = term_counts[token]
                if frequency <= 0:
                    continue
                df = document_frequencies[token]
                idf = math.log(((len(entries) - df + 0.5) / (df + 0.5)) + 1.0)
                denominator = frequency + 1.5 * (
                    1 - 0.75 + 0.75 * (len(tokens) / max(avg_doc_len, 1.0))
                )
                score += idf * ((frequency * 2.5) / max(denominator, 1e-6))
            if score > 0:
                hits.append(ScoredMemoryEntry(entry=entry, score=score, stage="bm25"))
        return hits

    def _emotional_hits(
        self,
        query_text: str,
        entries: list[MemoryEntry],
    ) -> list[ScoredMemoryEntry]:
        if not self.settings.emotional_enabled or self.emotional_query_embedder is None:
            return []
        query_embedding = self._safe_embed(self.emotional_query_embedder, query_text)
        if not query_embedding:
            return []
        hits: list[ScoredMemoryEntry] = []
        for entry in entries:
            if not entry.emotional_embedding:
                continue
            score = _cosine_similarity(query_embedding, entry.emotional_embedding)
            if score > 0:
                hits.append(ScoredMemoryEntry(entry=entry, score=score, stage="emotional"))
        return hits

    def _combine_stage_hits(
        self,
        stage_hits: list[ScoredMemoryEntry],
    ) -> dict[str, MemoryCandidate]:
        by_stage: dict[str, list[ScoredMemoryEntry]] = {}
        for hit in stage_hits:
            by_stage.setdefault(hit.stage, []).append(hit)

        combined_scores: dict[str, float] = {}
        combined_entries: dict[str, MemoryEntry] = {}
        combined_stages: dict[str, set[str]] = {}
        for hits in by_stage.values():
            for hit in _normalize_scores(hits):
                next_score = hit.score + 0.000001
                entry_id = hit.entry.entry_id
                combined_scores[entry_id] = combined_scores.get(entry_id, 0.0) + next_score
                combined_entries[entry_id] = hit.entry
                combined_stages.setdefault(entry_id, set()).add(hit.stage)
        return {
            entry_id: MemoryCandidate(
                entry=combined_entries[entry_id],
                score=combined_scores[entry_id],
                hit_stages=tuple(sorted(combined_stages.get(entry_id, set()))),
            )
            for entry_id in combined_entries
        }

    async def _rerank(
        self,
        query_text: str,
        candidates: list[MemoryCandidate],
        *,
        top_k: int,
    ) -> list[MemoryEntry] | None:
        if not self.settings.reranker_enabled or self.reranker is None or not candidates:
            return None
        try:
            ranked = self.reranker(query_text, candidates, top_k)
            if inspect.isawaitable(ranked):
                ranked = await ranked
        except Exception:
            return None
        if not ranked:
            return None
        seen: set[str] = set()
        deduped: list[MemoryEntry] = []
        for entry in ranked:
            if entry.entry_id in seen:
                continue
            seen.add(entry.entry_id)
            deduped.append(entry)
        return deduped

    def _safe_embed(
        self,
        embedder: QueryEmbedder,
        query_text: str,
    ) -> list[float] | None:
        try:
            return embedder(query_text)
        except Exception:
            return None


def _tokenize(text: str) -> list[str]:
    return [token for token in re.findall(r"\w+", text.lower()) if token]


def _normalize_scores(hits: list[ScoredMemoryEntry]) -> list[ScoredMemoryEntry]:
    if not hits:
        return []
    ordered = sorted(hits, key=lambda hit: hit.score, reverse=True)
    max_score = ordered[0].score
    min_score = ordered[-1].score
    if math.isclose(max_score, min_score):
        return [
            ScoredMemoryEntry(entry=hit.entry, score=1.0, stage=hit.stage)
            for hit in ordered
        ]
    return [
        ScoredMemoryEntry(
            entry=hit.entry,
            score=(hit.score - min_score) / (max_score - min_score),
            stage=hit.stage,
        )
        for hit in ordered
    ]


def _entry_debug(entry: MemoryEntry) -> dict[str, Any]:
    return {
        "entry_id": entry.entry_id,
        "created_at": entry.created_at,
        "source": entry.source,
        "interaction_partner": entry.interaction_partner,
        "content": entry.content,
    }


def _scored_hit_debug(hit: ScoredMemoryEntry) -> dict[str, Any]:
    return {
        **_entry_debug(hit.entry),
        "stage": hit.stage,
        "score": hit.score,
    }


def _candidate_debug(candidate: MemoryCandidate) -> dict[str, Any]:
    return {
        **_entry_debug(candidate.entry),
        "score": candidate.score,
        "hit_stages": list(candidate.hit_stages),
    }


def _cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    numerator = sum(a * b for a, b in zip(left, right, strict=False))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return numerator / (left_norm * right_norm)
