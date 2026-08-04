from __future__ import annotations

import asyncio

import regex

from chat.application.tools.common.canonical_token_budget import (
    count_canonical_tokens,
    truncate_canonical_prefix,
    truncate_canonical_suffix,
)
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

    __slots__ = (
        "_ranked_total_token_budget",
        "_ranked_window_builder",
        "_read_total_token_budget",
        "_read_window_builder",
        "_regex_context_side_token_budget",
        "_regex_total_token_budget",
        "_ranking_pipeline",
        "_store",
    )

    def __init__(
        self,
        *,
        read_window_token_budget: int,
        read_total_token_budget: int,
        ranked_window_token_budget: int,
        ranked_total_token_budget: int,
        regex_context_side_token_budget: int,
        regex_total_token_budget: int,
        ranking_pipeline: RankingPipeline,
        store: ToolContentStore,
    ) -> None:
        if min(
            read_window_token_budget,
            read_total_token_budget,
            ranked_window_token_budget,
            ranked_total_token_budget,
            regex_context_side_token_budget,
            regex_total_token_budget,
        ) < 1:
            raise ValueError("tool content token budgets must be greater than 0")
        self._ranking_pipeline = ranking_pipeline
        self._store = store
        self._read_window_builder = ToolContentWindowBuilder(
            token_budget=read_window_token_budget
        )
        self._ranked_window_builder = ToolContentWindowBuilder(
            token_budget=ranked_window_token_budget
        )
        self._read_total_token_budget = read_total_token_budget
        self._ranked_total_token_budget = ranked_total_token_budget
        self._regex_context_side_token_budget = regex_context_side_token_budget
        self._regex_total_token_budget = regex_total_token_budget

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
            window=self._read_window_builder.build_range_window(
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
        windows = []
        remaining = self._read_total_token_budget
        for locator in locators:
            if remaining <= 0:
                break
            window = self._read_window_builder.build_range_window(
                stored,
                start=locator.start_offset,
                end=locator.end_offset,
                token_budget=remaining,
            )
            windows.append(window)
            remaining -= count_canonical_tokens(window.text)
        return ToolContentLocatorReadResult(
            content_id=content_id,
            locator=locator_name,
            windows=tuple(windows),
            budget_exhausted=len(windows) < len(locators),
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

        def scan_loaded() -> tuple[tuple[ToolContentRegexMatch, ...], bool]:
            try:
                compiled = regex.compile(request.pattern)
            except regex.error as exc:
                raise ToolContentInvalidRegexError(str(exc)) from exc

            max_matches = max(request.max_matches, 0)
            matches: list[ToolContentRegexMatch] = []
            remaining = self._regex_total_token_budget
            for content_id, stored in stored_items:
                try:
                    for matched in compiled.finditer(
                        stored.text,
                        timeout=_SEARCH_TIMEOUT_SECONDS,
                    ):
                        if remaining <= 0:
                            return tuple(matches), True
                        window_start, window_end = _regex_window_range(
                            stored.text,
                            match_start=matched.start(),
                            match_end=matched.end(),
                            context_chars=request.context_chars,
                            context_side_token_budget=self._regex_context_side_token_budget,
                            total_token_budget=remaining,
                        )
                        window = self._read_window_builder.build_range_window(
                            stored,
                            start=window_start,
                            end=window_end,
                            token_budget=remaining,
                        )
                        matches.append(
                            ToolContentRegexMatch(
                                content_id=content_id,
                                match_start=matched.start(),
                                match_end=matched.end(),
                                window=window,
                            )
                        )
                        remaining -= count_canonical_tokens(window.text)
                        if len(matches) >= max_matches:
                            return tuple(matches), False
                except TimeoutError as exc:
                    raise ToolContentRegexTimeoutError(
                        f"regex search exceeded {_SEARCH_TIMEOUT_SECONDS}s"
                    ) from exc
            return tuple(matches), False

        matches, budget_exhausted = (
            await asyncio.to_thread(scan_loaded)
            if request.max_matches > 0
            else ((), False)
        )
        return ToolContentRegexReadResult(
            matches=matches,
            failed=failed,
            budget_exhausted=budget_exhausted,
        )

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
        remaining = self._ranked_total_token_budget
        budget_exhausted = False
        for item in result.ranked:
            if remaining <= 0:
                budget_exhausted = True
                break
            source = sources.get(item.candidate_id)
            if source is None:
                continue
            content_id, stored, chunk = source
            window = self._ranked_window_builder.build_source_window(
                stored,
                chunk=chunk,
                token_budget=remaining,
            )
            ranked.append(
                ToolContentRankedReadItem(
                    content_id=content_id,
                    rank=item.rank,
                    score=item.score,
                    chunk_index=chunk.chunk_index,
                    window=window,
                )
            )
            remaining -= count_canonical_tokens(window.text)
        return ToolContentRankedReadResult(
            ranked=tuple(ranked),
            failed=failed,
            budget_exhausted=budget_exhausted,
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


def _regex_window_range(
    text: str,
    *,
    match_start: int,
    match_end: int,
    context_chars: int | None,
    context_side_token_budget: int,
    total_token_budget: int,
) -> tuple[int, int]:
    if context_chars is None:
        _, before_start, _ = truncate_canonical_suffix(
            text[:match_start],
            context_side_token_budget,
        )
        _, after_length, _ = truncate_canonical_prefix(
            text[match_end:],
            context_side_token_budget,
        )
        candidate_start = before_start
        candidate_end = match_end + after_length
    else:
        context_chars = max(context_chars, 0)
        candidate_start = max(match_start - context_chars, 0)
        candidate_end = min(match_end + context_chars, len(text))

    if count_canonical_tokens(text[candidate_start:candidate_end]) <= total_token_budget:
        return candidate_start, candidate_end

    match_tokens = count_canonical_tokens(text[match_start:match_end])
    if match_tokens >= total_token_budget:
        return match_start, match_end

    context_budget = total_token_budget - match_tokens
    before_budget = context_budget // 2
    _, before_offset, _ = truncate_canonical_suffix(
        text[candidate_start:match_start],
        before_budget,
    )
    start = candidate_start + before_offset
    after_budget = context_budget - count_canonical_tokens(text[start:match_start])
    _, after_length, _ = truncate_canonical_prefix(
        text[match_end:candidate_end],
        after_budget,
    )
    end = match_end + after_length
    while count_canonical_tokens(text[start:end]) > total_token_budget and end > match_end:
        end -= 1
    while count_canonical_tokens(text[start:end]) > total_token_budget and start < match_start:
        start += 1
    return start, end
