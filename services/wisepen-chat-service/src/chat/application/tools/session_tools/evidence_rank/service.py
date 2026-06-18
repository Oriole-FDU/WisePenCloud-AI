from __future__ import annotations

import asyncio
from dataclasses import dataclass

from chat.application.tools.common.tool_content_store import ToolContentStore
from chat.application.tools.common.tool_content_store.models import (
    StoredToolContent,
)
from chat.application.tools.session_tools.evidence_rank.models import EvidenceRankItem
from chat.application.utils.ranking_engine import (
    RankCandidate,
    RankQuery,
    RankRequest,
    RankingEngine,
)
from chat.application.utils.ranking_engine import get_ranking_engine


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
    """跨多个工具内容存储凭证（ToolContent）的全局二次精排服务。"""

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
        """对批量的缓存内容进行跨实体的全局打分重排，仅返回高价值的定位索引。"""
        # 1. 并发拉取并还原所有底层的工具输出载荷
        fetched = await asyncio.gather(
            *(
                self._fetch_content(content_id=cid, session_id=session_id)
                for cid in content_ids
            )
        )

        found: dict[str, StoredToolContent] = {}
        found_order: list[str] = []

        # 过滤空数据并去重保留原始序列
        for item in fetched:
            if item.stored is None or item.content_id in found:
                continue
            found[item.content_id] = item.stored
            found_order.append(item.content_id)

        # 2. 将所有文档的切片块扁平化连接，确保重排处于全局同一基准线
        evidence_candidates = tuple(
            candidate
            for cid in found_order
            for candidate in self._build_evidence_candidates(found[cid])
        )

        if not evidence_candidates:
            return ()

        # 构建快速索引映射表
        source_by_candidate_id = {
            c.candidate_id: c for c in evidence_candidates
        }

        # 3. 规整并换算重排引擎的标准输入格式
        rank_candidates = tuple(
            RankCandidate(
                candidate_id=c.candidate_id,
                text=c.text,
                metadata={
                    "content_id": c.stored.content_id,
                    "chunk_index": c.chunk_index,
                },
            )
            for c in evidence_candidates
        )

        # 4. 驱动异步重排交叉双塔模型
        result = await self._ranking_engine.rank_async(
            RankRequest(
                query=RankQuery(text=query),
                candidates=rank_candidates,
                top_k=max(max_evidence, 0),
                candidate_limit=len(rank_candidates),
            )
        )

        # 5. 回填定位凭证并规整输出结构
        items: list[EvidenceRankItem] = []
        for ranked in result.ranked:
            source = source_by_candidate_id[ranked.candidate_id]
            items.append(
                EvidenceRankItem(
                    content_id=source.stored.content_id,
                    chunk_index=source.chunk_index,
                )
            )

        return tuple(items)

    async def _fetch_content(
        self,
        *,
        content_id: str,
        session_id: str,
    ) -> _FetchedContent:
        """换算真实重定向标识并拉取内容实体。"""
        canonical_id, _ = await self._store.canonicalize_content_id(
            content_id=content_id,
            session_id=session_id,
        )
        stored = await self._store.get(
            content_id=canonical_id,
            session_id=session_id,
        )
        return _FetchedContent(content_id=canonical_id, stored=stored)

    @staticmethod
    def _build_evidence_candidates(
        stored: StoredToolContent,
    ) -> tuple[_EvidenceCandidate, ...]:
        """将内容文本细化拆解为符合重排粒度的候选证据颗粒。"""
        # 退化保护：针对无结构、无分块的数据进行全文字串兜底
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

        # 矩阵物理切片映射转换
        candidates = []
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
