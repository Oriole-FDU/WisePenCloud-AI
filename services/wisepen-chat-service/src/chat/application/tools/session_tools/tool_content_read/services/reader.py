from __future__ import annotations

import asyncio

import regex

from chat.application.tools.common.tool_content_store import (
    StoredToolContent,
    ToolContentChunk,
    ToolContentStore,
)
from chat.application.utils.ranking import RankCandidate, RankQuery, RankRequest
from chat.application.utils.ranking.pipeline import RankingPipeline

from .chunk_selection import select_chunks
from .content_window_builder import ToolContentWindowBuilder, chunk_text
from .models import (
    ToolContentRankedExpandItem,
    ToolContentRankedExpandReadRequest,
    ToolContentRankedExpandReadResult,
    ToolContentReadFailure,
    ToolContentReadResult,
    ToolContentRegexMatch,
    ToolContentRegexReadRequest,
    ToolContentRegexReadResult,
)

_SEARCH_TIMEOUT_SECONDS = 0.05


class ToolContentInvalidRegexError(ValueError):
    """正则表达式语法无效。"""


class ToolContentRegexTimeoutError(TimeoutError):
    """单次正则搜索超过执行时间限制。"""


class ToolContentReader:
    """读取工具缓存正文，支持区间读取、正则定位和排序扩展。"""

    __slots__ = ("_ranking_pipeline", "_store", "_window_builder")

    def __init__(
        self,
        *,
        max_window_chars: int | None,
        ranking_pipeline: RankingPipeline,
        store: ToolContentStore,
    ) -> None:
        self._ranking_pipeline = ranking_pipeline
        self._store = store
        self._window_builder = ToolContentWindowBuilder(
            max_chars=max_window_chars,
        )

    async def read_range(
        self,
        *,
        content_id: str,
        session_id: str,
        start: int | None,
        end: int | None,
    ) -> ToolContentReadResult:
        stored = await self._store.get(
            content_id=content_id,
            session_id=session_id,
        )
        if stored is None:
            return ToolContentReadResult(
                content_id=content_id,
                reason="content_not_found",
            )

        return ToolContentReadResult(
            content_id=content_id,
            window=await asyncio.to_thread(
                self._window_builder.build_range_window,
                stored,
                start=start,
                end=end,
            ),
        )

    async def read_regex(
        self,
        *,
        request: ToolContentRegexReadRequest,
        session_id: str,
    ) -> ToolContentRegexReadResult:
        stored_items, failed = await self._load_many(
            content_ids=request.content_ids,
            session_id=session_id,
        )

        def scan_loaded() -> tuple[ToolContentRegexMatch, ...]:
            try:
                compiled = regex.compile(request.pattern)
            except regex.error as exc:
                raise ToolContentInvalidRegexError(str(exc)) from exc

            max_matches = max(request.max_matches, 0)
            if not max_matches:
                return ()

            matches: list[ToolContentRegexMatch] = []

            # regex 库支持 timeout，避免模型传入灾难性表达式拖死执行线程。
            for content_id, stored in stored_items:
                chunks = select_chunks(stored, request.selector)

                for chunk in chunks:
                    matched_window = None
                    try:
                        for _matched in compiled.finditer(
                            chunk_text(stored, chunk),
                            timeout=_SEARCH_TIMEOUT_SECONDS,
                        ):
                            if matched_window is None:
                                matched_window = (
                                    self._window_builder.build_expanded_window(
                                        stored,
                                        chunks=chunks,
                                        center_chunk=chunk.chunk_index,
                                        merge_before=request.merge_before,
                                        merge_after=request.merge_after,
                                    )
                                )

                            matches.append(
                                ToolContentRegexMatch(
                                    content_id=content_id,
                                    window=matched_window,
                                )
                            )

                            if len(matches) >= max_matches:
                                return tuple(matches)
                    except TimeoutError as exc:
                        raise ToolContentRegexTimeoutError(
                            f"regex search exceeded {_SEARCH_TIMEOUT_SECONDS}s"
                        ) from exc

            return tuple(matches)

        matches = await asyncio.to_thread(scan_loaded)

        return ToolContentRegexReadResult(
            matches=matches,
            failed=failed,
        )

    async def read_ranked_expand(
        self,
        *,
        request: ToolContentRankedExpandReadRequest,
        session_id: str,
    ) -> ToolContentRankedExpandReadResult:
        stored_items, failed = await self._load_many(
            content_ids=request.content_ids,
            session_id=session_id,
        )

        def build_candidates() -> tuple[
            tuple[RankCandidate, ...],
            dict[str, tuple[str, StoredToolContent, int]],
            dict[str, tuple[ToolContentChunk, ...]],
        ]:
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

                    # ranking 只保存轻量候选，真实窗口在排序完成后再构造。
                    sources[candidate_id] = (
                        content_id,
                        stored,
                        chunk.chunk_index,
                    )

                    candidates.append(
                        RankCandidate(
                            candidate_id=candidate_id,
                            text=text,
                            fields={
                                "section": " ".join(
                                    " / ".join(path) for path in chunk.section_paths
                                ),
                                "anchor": " ".join(chunk.anchor_labels),
                            },
                            metadata={
                                "content_id": content_id,
                                "chunk_index": chunk.chunk_index,
                            },
                            group_key=content_id,
                        )
                    )

            return tuple(candidates), sources, chunks_by_content_id

        # chunk 遍历和文本处理属于 CPU 工作，避免阻塞事件循环。
        candidates, sources, chunks_by_content_id = await asyncio.to_thread(
            build_candidates,
        )

        if not candidates:
            return ToolContentRankedExpandReadResult(
                failed=failed,
            )

        result = await self._ranking_pipeline.arank(
            RankRequest(
                query=RankQuery(text=request.query.strip()),
                candidates=candidates,
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

        return ToolContentRankedExpandReadResult(
            ranked=tuple(ranked),
            failed=failed,
        )

    async def _load_many(
        self,
        *,
        content_ids: tuple[str, ...],
        session_id: str,
    ) -> tuple[
        tuple[tuple[str, StoredToolContent], ...],
        tuple[ToolContentReadFailure, ...],
    ]:
        async def load_one(
            content_id: str,
        ) -> tuple[StoredToolContent | None, ToolContentReadFailure | None]:
            try:
                stored = await self._store.get(
                    content_id=content_id,
                    session_id=session_id,
                )
            except Exception as exc:
                return None, ToolContentReadFailure(
                    content_id=content_id,
                    reason=type(exc).__name__,
                )

            if stored is None:
                return None, ToolContentReadFailure(
                    content_id=content_id,
                    reason="content_not_found",
                )

            return stored, None

        loaded_items = await asyncio.gather(
            *(load_one(content_id) for content_id in content_ids)
        )

        stored_items: list[tuple[str, StoredToolContent]] = []
        failed: list[ToolContentReadFailure] = []
        for content_id, (stored, failure) in zip(content_ids, loaded_items):
            if failure is not None:
                failed.append(failure)
            elif stored is not None:
                stored_items.append((content_id, stored))

        return tuple(stored_items), tuple(failed)
