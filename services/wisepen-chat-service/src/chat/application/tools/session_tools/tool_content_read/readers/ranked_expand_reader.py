from __future__ import annotations

from chat.application.tools.common.tool_content_store.core.models import (
    StoredToolContent,
    ToolContentChunk,
)
from chat.application.tools.session_tools.tool_content_read.content_window_builder import (
    ToolContentWindowBuilder,
)
from chat.application.tools.session_tools.tool_content_read.content_loader import (
    ToolContentLoader,
)
from chat.application.tools.session_tools.tool_content_read.models import (
    ToolContentReadMatch,
    ToolContentReadResult,
    ToolContentRerankReadRequest,
)
from chat.application.tools.session_tools.tool_content_read.chunk_selector import (
    select_chunks,
)
from chat.application.utils.ranking_engine.engine import RankingEngine
from chat.application.utils.ranking_engine.models import (
    RankCandidate,
    RankQuery,
    RankRequest,
)


class RankedExpandReader:
    """跨文档语义检索 reader：选择候选、排序、构造扩展窗口。"""

    __slots__ = ("_loader", "_ranking_engine", "_window_builder")

    def __init__(
        self,
        *,
        loader: ToolContentLoader,
        ranking_engine: RankingEngine,
        window_builder: ToolContentWindowBuilder,
    ) -> None:
        self._loader = loader
        self._ranking_engine = ranking_engine
        self._window_builder = window_builder

    async def read(
        self,
        *,
        request: ToolContentRerankReadRequest,
        session_id: str,
    ) -> ToolContentReadResult:
        stored_items, failed = await self._loader.load_many(
            content_ids=request.content_ids,
            session_id=session_id,
        )
        matches = await self._read_loaded(
            stored_items=stored_items,
            request=request,
        )
        return ToolContentReadResult(matches=matches, failed=failed)

    async def _read_loaded(
        self,
        *,
        stored_items: tuple[tuple[str, StoredToolContent], ...],
        request: ToolContentRerankReadRequest,
    ) -> tuple[ToolContentReadMatch, ...]:
        query = request.query.strip()
        candidates: list[RankCandidate] = []
        source_by_candidate_id: dict[str, tuple[str, StoredToolContent, int]] = {}
        chunks_by_content_id: dict[str, tuple[ToolContentChunk, ...]] = {}

        for content_id, stored in stored_items:
            candidate_chunks = select_chunks(stored, request.selector)
            chunks_by_content_id[content_id] = candidate_chunks
            for chunk in candidate_chunks:
                text = ToolContentWindowBuilder.chunk_text(stored, chunk)
                if not text:
                    continue

                candidate_id = f"{content_id}:chunk:{chunk.chunk_index}"
                source_by_candidate_id[candidate_id] = (
                    content_id,
                    stored,
                    chunk.chunk_index,
                )
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

        ranked = (
            await self._ranking_engine.rank_async(
                RankRequest(
                    query=RankQuery(text=query),
                    candidates=tuple(candidates),
                    top_k=max(request.top_k, 0),
                    candidate_limit=len(candidates),
                )
            )
        ).ranked

        matches: list[ToolContentReadMatch] = []
        for item in ranked:
            source = source_by_candidate_id.get(item.candidate_id)
            if source is None:
                continue

            content_id, stored, chunk_index = source
            matches.append(
                ToolContentReadMatch(
                    content_id=content_id,
                    window=self._window_builder.expand(
                        stored,
                        chunks=chunks_by_content_id[content_id],
                        center_chunk=chunk_index,
                        merge_before=request.merge_before,
                        merge_after=request.merge_after,
                    ),
                )
            )

        return tuple(matches)
