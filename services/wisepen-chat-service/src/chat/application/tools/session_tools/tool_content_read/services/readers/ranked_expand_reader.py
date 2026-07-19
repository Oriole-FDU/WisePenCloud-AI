from __future__ import annotations

from chat.application.tools.common.tool_content_store import (
    StoredToolContent,
    ToolContentChunk,
)
from chat.application.utils.ranking import RankCandidate, RankQuery, RankRequest
from chat.application.utils.ranking.pipeline import RankingPipeline

from ..content_loader import ToolContentLoader
from ..content_window_builder import ToolContentWindowBuilder, chunk_text
from ..models import (
    ToolContentRankedExpandItem,
    ToolContentRankedExpandReadRequest,
    ToolContentRankedExpandReadResult,
)
from ._utils.chunk_selection import select_chunks


class RankedExpandReader:
    """选择候选 chunk、全局重排并构造扩展窗口。"""

    __slots__ = ("_loader", "_ranking_pipeline", "_window_builder")

    def __init__(
            self,
            *,
            loader: ToolContentLoader,
            ranking_pipeline: RankingPipeline,
            window_builder: ToolContentWindowBuilder,
    ) -> None:
        self._loader = loader
        self._ranking_pipeline = ranking_pipeline
        self._window_builder = window_builder

    async def read(
            self,
            *,
            request: ToolContentRankedExpandReadRequest,
            session_id: str,
    ) -> ToolContentRankedExpandReadResult:
        stored_items, failed = await self._loader.load_many(
            content_ids=request.content_ids,
            session_id=session_id,
        )
        ranked = await self._read_loaded(stored_items=stored_items, request=request)
        return ToolContentRankedExpandReadResult(ranked=ranked, failed=failed)

    async def _read_loaded(
            self,
            *,
            stored_items: tuple[tuple[str, StoredToolContent], ...],
            request: ToolContentRankedExpandReadRequest,
    ) -> tuple[ToolContentRankedExpandItem, ...]:
        candidates: list[RankCandidate] = []
        sources: dict[str, tuple[str, StoredToolContent, int]] = {}
        chunks_by_content_id: dict[str, tuple[ToolContentChunk, ...]] = {}

        for content_id, stored in stored_items:
            chunks = select_chunks(stored, request.selector)
            chunks_by_content_id[content_id] = chunks
            for chunk in chunks:
                text = chunk_text(stored, chunk)
                if not text:
                    continue

                candidate_id = f"{content_id}:chunk:{chunk.chunk_index}"
                sources[candidate_id] = content_id, stored, chunk.chunk_index
                candidates.append(
                    RankCandidate(
                        candidate_id=candidate_id,
                        text=text,
                        fields={
                            "section": " / ".join(chunk.section_path),
                            "anchor": " ".join(chunk.anchor_labels),
                        },
                        metadata={
                            "content_id": content_id,
                            "chunk_index": chunk.chunk_index,
                        },
                        group_key=content_id,
                    )
                )

        if not candidates:
            return ()

        result = await self._ranking_pipeline.arank(
            RankRequest(
                query=RankQuery(text=request.query.strip()),
                candidates=tuple(candidates),
                top_k=max(request.top_k, 0),
                candidate_limit=len(candidates),
            )
        )
        ranked: list[ToolContentRankedExpandItem] = []
        for item in result.ranked:
            source = sources.get(item.candidate_id)
            if source is None:
                continue

            content_id, stored, chunk_index = source
            ranked.append(
                ToolContentRankedExpandItem(
                    content_id=content_id,
                    rank=item.rank,
                    score=item.score,
                    window=self._window_builder.build_expanded_window(
                        stored,
                        chunks=chunks_by_content_id[content_id],
                        center_chunk=chunk_index,
                        merge_before=request.merge_before,
                        merge_after=request.merge_after,
                    ),
                )
            )

        return tuple(ranked)
