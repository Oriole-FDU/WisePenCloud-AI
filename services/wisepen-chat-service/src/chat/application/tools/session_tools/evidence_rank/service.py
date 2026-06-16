from __future__ import annotations

import asyncio
from dataclasses import dataclass

from chat.application.utils.ranking_engine.core import (
    RankCandidate,
    RankQuery,
    RankRequest,
    RankingEngine,
)

from chat.application.tools.common.tool_content_store import ToolContentStore
from chat.application.tools.common.tool_content_store.models import (
    StoredToolContent,
)
from chat.application.tools.session_tools.evidence_rank.models import EvidenceRankItem
from chat.application.utils.ranking_engine.factory import get_ranking_engine


@dataclass(frozen=True, slots=True)
class _FetchedContent:
    content_id: str
    stored: StoredToolContent | None


@dataclass(frozen=True, slots=True)
class _EvidenceCandidate:
    stored: StoredToolContent
    candidate_id: str
    text: str
    chunk_index: int | None


class EvidenceRankService:
    """跨多个 ToolContent 做一次二次精排。"""

    __slots__ = ("_ranking_engine", "_store")

    def __init__(
        self,
        *,
        store: ToolContentStore,
        ranking_engine: RankingEngine | None = None,
    ) -> None:
        self._store = store
        self._ranking_engine = ranking_engine or get_ranking_engine("session.evidence_rank")

    async def rank(
        self,
        *,
        query: str,
        content_ids: tuple[str, ...],
        session_id: str,
        max_evidence: int,
    ) -> tuple[EvidenceRankItem, ...]:
        """对一批已缓存内容做一次跨 content_id 精排。

        Args:
            query: 二次精排使用的窄查询。
            content_ids: 会话内 ToolContentStore 的 cnt_* 引用。
            session_id: 会话隔离键。
            max_evidence: 返回的证据定位数量上限。

        Returns:
            只包含 content_id、chunk_index、rank、score 的定位结果；正文和结构元信息
            由后续 tool_content_read 直接展开时注入。
        """
        fetched = await asyncio.gather(
            *(
                self._fetch_content(content_id=content_id, session_id=session_id)
                for content_id in content_ids
            )
        )

        found: dict[str, StoredToolContent] = {}
        found_order: list[str] = []

        for item in fetched:
            if item.stored is None:
                continue
            if item.content_id in found:
                continue
            found[item.content_id] = item.stored
            found_order.append(item.content_id)

        # 这里合并所有 content 的 chunk 后只调用一次 reranker，保证排名是跨 content_id 的全局顺序。
        evidence_candidates = tuple(
            candidate
            for content_id in found_order
            for candidate in self._build_evidence_candidates(found[content_id])
        )

        if not evidence_candidates:
            return ()

        source_by_candidate_id = {
            candidate.candidate_id: candidate
            for candidate in evidence_candidates
        }
        # metadata 只放回填定位所需字段；不要在 evidence_rank 返回中暴露展示元信息。
        rank_candidates = tuple(
            RankCandidate(
                candidate_id=candidate.candidate_id,
                text=candidate.text,
                metadata={
                    "content_id": candidate.stored.content_id,
                    "chunk_index": candidate.chunk_index,
                },
            )
            for candidate in evidence_candidates
        )

        result = await self._ranking_engine.rank_async(
            RankRequest(
                query=RankQuery(text=query),
                candidates=rank_candidates,
                top_k=max(max_evidence, 0),
                candidate_limit=len(rank_candidates),
            )
        )

        items = tuple(
            self._to_result_item(
                ranked_item=ranked_item,
                source=source_by_candidate_id[ranked_item.candidate_id],
            )
            for ranked_item in result.ranked
        )

        return items

    async def _fetch_content(
        self,
        *,
        content_id: str,
        session_id: str,
    ) -> _FetchedContent:
        canonical_content_id, _ = await self._store.canonicalize_content_id(
            content_id=content_id,
            session_id=session_id,
        )
        stored = await self._store.get(
            content_id=canonical_content_id,
            session_id=session_id,
        )
        return _FetchedContent(
            content_id=canonical_content_id,
            stored=stored,
        )

    @staticmethod
    def _build_evidence_candidates(
        stored: StoredToolContent,
    ) -> tuple[_EvidenceCandidate, ...]:
        """把 StoredToolContent 展开为 reranker 候选。"""
        if not stored.chunks:
            text = stored.text.strip()
            if not text:
                return ()
            return (
                _EvidenceCandidate(
                    stored=stored,
                    candidate_id=f"{stored.content_id}:content",
                    text=text,
                    chunk_index=None,
                ),
            )

        candidates: list[_EvidenceCandidate] = []
        for chunk in sorted(stored.chunks, key=lambda item: item.chunk_index):
            if chunk.start_offset is None or chunk.end_offset is None:
                text = ""
            else:
                text = stored.text[chunk.start_offset:chunk.end_offset].strip()
            if not text:
                continue
            candidates.append(
                _EvidenceCandidate(
                    stored=stored,
                    candidate_id=f"{stored.content_id}:chunk:{chunk.chunk_index}",
                    text=text,
                    chunk_index=chunk.chunk_index,
                )
            )
        return tuple(candidates)

    @staticmethod
    def _to_result_item(
        *,
        ranked_item,
        source: _EvidenceCandidate,
    ) -> EvidenceRankItem:
        stored = source.stored
        return EvidenceRankItem(
            content_id=stored.content_id,
            chunk_index=source.chunk_index,
        )
