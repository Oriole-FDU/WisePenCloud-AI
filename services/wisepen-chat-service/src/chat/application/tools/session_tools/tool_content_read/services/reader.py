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

from .content_window_builder import ToolContentWindowBuilder, chunk_text
from .models import (
    ToolContentLocatorReadResult,
    ToolContentReadFailure,
    ToolContentReadResult,
    ToolContentRegexMatch,
    ToolContentRegexReadRequest,
    ToolContentRegexReadResult,
    ToolContentRankedReadItem,
    ToolContentRankedReadRequest,
    ToolContentRankedReadResult,
    ToolContentSnapshotLocator,
    ToolContentSnapshotResult,
)

_SEARCH_TIMEOUT_SECONDS = 0.05


class ToolContentInvalidRegexError(ValueError):
    """正则表达式语法无效。"""


class ToolContentRegexTimeoutError(TimeoutError):
    """单次正则搜索超过执行时间限制。"""


class ToolContentReader:
    """按 offset、locator、正则或语义检索读取权威工具原文。"""

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
        self._window_builder = ToolContentWindowBuilder(max_chars=max_window_chars)

    async def read_range(
        self,
        *,
        content_id: str,
        session_id: str,
        start: int | None,
        end: int | None,
    ) -> ToolContentReadResult:
        stored = await self._store.get(content_id=content_id, session_id=session_id)
        if stored is None:
            return ToolContentReadResult(content_id=content_id, reason="content_not_found")
        return ToolContentReadResult(
            content_id=content_id,
            window=self._window_builder.build_range_window(
                stored,
                start=start,
                end=end,
            ),
        )

    async def read_locator(
        self,
        *,
        content_id: str,
        session_id: str,
        locator_name: str,
    ) -> ToolContentLocatorReadResult:
        stored = await self._store.get(content_id=content_id, session_id=session_id)
        if stored is None:
            return ToolContentLocatorReadResult(
                content_id=content_id,
                locator=locator_name,
                reason="content_not_found",
            )
        locators = tuple(
            locator for locator in stored.locators if locator.name == locator_name
        )
        if not locators:
            return ToolContentLocatorReadResult(
                content_id=content_id,
                locator=locator_name,
                reason="locator_not_found",
            )
        return ToolContentLocatorReadResult(
            content_id=content_id,
            locator=locator_name,
            windows=tuple(
                self._window_builder.build_range_window(
                    stored,
                    start=locator.start_offset,
                    end=locator.end_offset,
                )
                for locator in locators
            ),
        )

    async def get_snapshot(
        self,
        *,
        content_id: str,
        session_id: str,
    ) -> ToolContentSnapshotResult:
        stored = await self._store.get(content_id=content_id, session_id=session_id)
        if stored is None:
            return ToolContentSnapshotResult(
                content_id=content_id,
                reason="content_not_found",
            )
        return ToolContentSnapshotResult(
            content_id=content_id,
            content_type=stored.content_type,
            total_length=len(stored.text),
            locators=tuple(
                ToolContentSnapshotLocator(
                    locator_index=index,
                    name=locator.name,
                    kind=locator.kind,
                    start_offset=locator.start_offset,
                    end_offset=locator.end_offset,
                )
                for index, locator in enumerate(stored.locators)
            ),
            metadata=dict(stored.metadata),
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
            context_chars = max(request.context_chars, 0)
            matches: list[ToolContentRegexMatch] = []
            for content_id, stored in stored_items:
                try:
                    for matched in compiled.finditer(
                        stored.text,
                        timeout=_SEARCH_TIMEOUT_SECONDS,
                    ):
                        matches.append(
                            ToolContentRegexMatch(
                                content_id=content_id,
                                match_start=matched.start(),
                                match_end=matched.end(),
                                window=self._window_builder.build_range_window(
                                    stored,
                                    start=max(matched.start() - context_chars, 0),
                                    end=min(
                                        matched.end() + context_chars,
                                        len(stored.text),
                                    ),
                                ),
                            )
                        )
                        if len(matches) >= max_matches:
                            return tuple(matches)
                except TimeoutError as exc:
                    raise ToolContentRegexTimeoutError(
                        f"regex search exceeded {_SEARCH_TIMEOUT_SECONDS}s"
                    ) from exc
            return tuple(matches)

        matches = await asyncio.to_thread(scan_loaded) if request.max_matches > 0 else ()
        return ToolContentRegexReadResult(matches=matches, failed=failed)

    async def read_ranked(
        self,
        *,
        request: ToolContentRankedReadRequest,
        session_id: str,
    ) -> ToolContentRankedReadResult:
        stored_items, failed = await self._load_many(
            content_ids=request.content_ids,
            session_id=session_id,
        )

        candidates: list[RankCandidate] = []
        sources: dict[
            str,
            tuple[str, StoredToolContent, ToolContentChunk],
        ] = {}
        for content_id, stored in stored_items:
            for chunk in stored.chunks:
                text = chunk_text(stored, chunk)
                if not text:
                    continue
                candidate_id = f"{content_id}:chunk:{chunk.chunk_index}"
                sources[candidate_id] = (content_id, stored, chunk)
                candidates.append(
                    RankCandidate(
                        candidate_id=candidate_id,
                        text=text,
                        fields={
                            "section": "\n".join(
                                " > ".join(path) for path in chunk.section_paths
                            ),
                            "anchor": "\n".join(chunk.anchor_labels),
                        },
                        group_key=content_id,
                    )
                )

        if not candidates or request.top_k <= 0:
            return ToolContentRankedReadResult(failed=failed)
        result = await self._ranking_pipeline.arank(
            RankRequest(
                query=RankQuery(text=request.query.strip()),
                candidates=tuple(candidates),
                top_k=request.top_k,
                candidate_limit=len(candidates),
            )
        )

        ranked: list[ToolContentRankedReadItem] = []
        for item in result.ranked:
            source = sources.get(item.candidate_id)
            if source is None:
                continue
            content_id, stored, chunk = source
            ranked.append(
                ToolContentRankedReadItem(
                    content_id=content_id,
                    rank=item.rank,
                    score=item.score,
                    chunk_index=chunk.chunk_index,
                    window=self._window_builder.build_source_window(
                        stored,
                        chunk=chunk,
                    ),
                )
            )
        return ToolContentRankedReadResult(ranked=tuple(ranked), failed=failed)

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
