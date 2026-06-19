from __future__ import annotations

import re

from chat.application.tools.common.tool_content_store import ToolContentStore
from chat.application.tools.common.tool_content_store.models import StoredToolContent, ToolContentChunk
from chat.application.tools.session_tools.tool_content_read.models import (
    ToolContentReadMatch,
    ToolContentReadMode,
    ToolContentReadRequest,
    ToolContentReadResult,
    ToolContentSelector,
    ToolContentWindow,
)
from chat.application.tools.session_tools.utils.content_window_builder import ToolContentWindowBuilder
from chat.application.tools.tool_settings import tool_settings
from chat.application.utils.ranking_engine import (
    RankCandidate,
    RankQuery,
    RankRequest,
    RankingEngine,
)
from chat.application.utils.ranking_engine import get_ranking_engine

MAX_REGEX_PATTERN_CHARS = tool_settings.TOOL_CONTENT_READ_MAX_REGEX_PATTERN_CHARS


class _RegexLimitReached(Exception):
    pass


class ToolContentReadService:
    """跨文档内容检索服务，支持 ranked_expand 与 regex_match。"""

    __slots__ = ("_store", "_ranking_engine")

    def __init__(
            self,
            *,
            store: ToolContentStore,
            ranking_engine: RankingEngine | None = None,
    ) -> None:
        self._store = store
        self._ranking_engine = ranking_engine or get_ranking_engine("services.ranked_expand")

    async def read(
            self,
            *,
            request: ToolContentReadRequest,
            session_id: str,
    ) -> ToolContentReadResult:
        self._validate_request(request)

        stored_items: list[tuple[str, StoredToolContent]] = []
        failed: list[ToolContentReadMatch] = []
        for content_id in request.content_ids:
            # 先把每个 content_id 解析为可读正文；单项失败保留在 failed，不拖垮整次检索。
            loaded = await self._load_one(content_id=content_id, session_id=session_id)
            if isinstance(loaded, ToolContentReadMatch):
                failed.append(loaded)
                continue
            stored_items.append(loaded)

        if request.mode == ToolContentReadMode.RANKED_EXPAND:
            matches = await self._read_ranked_expand_across_contents(
                stored_items=tuple(stored_items),
                request=request,
            )
        elif request.mode == ToolContentReadMode.REGEX_MATCH:
            matches = self._read_regex_match_across_contents(
                stored_items=tuple(stored_items),
                request=request,
            )
        else:
            raise ValueError(f"Unsupported read mode: {request.mode}")

        return ToolContentReadResult(
            mode=request.mode,
            matches=matches,
            failed=tuple(failed),
        )

    @staticmethod
    def _validate_request(request: ToolContentReadRequest) -> None:
        if request.mode == ToolContentReadMode.RANKED_EXPAND and not (request.query or "").strip():
            raise ValueError("ranked_expand requires query.")

        if request.mode == ToolContentReadMode.REGEX_MATCH:
            pattern = request.pattern or ""
            if not pattern:
                raise ValueError("regex_match requires pattern.")
            if len(pattern) > MAX_REGEX_PATTERN_CHARS:
                raise ValueError(f"regex pattern is too long; max {MAX_REGEX_PATTERN_CHARS} chars.")

    async def load_stored_content(
            self,
            *,
            content_id: str,
            session_id: str,
    ) -> tuple[str, StoredToolContent] | None:
        canonical_id, _ = await self._store.canonicalize_content_id(
            content_id=content_id,
            session_id=session_id,
        )
        stored = await self._store.get(content_id=canonical_id, session_id=session_id)
        if stored is None:
            return None
        return canonical_id, stored

    def build_continuous_window(
            self,
            *,
            stored: StoredToolContent,
            offset: int,
            limit: int,
    ) -> ToolContentWindow:
        safe_offset = max(offset, 0)
        safe_limit = max(limit, 0)
        end = min(len(stored.text), safe_offset + safe_limit)

        chunks = tuple(
            chunk
            for chunk in stored.chunks
            if chunk.start_offset is not None
            and chunk.end_offset is not None
            and chunk.start_offset < end
            and chunk.end_offset > safe_offset
        )
        locator = ToolContentWindowBuilder.locator(stored, chunks)
        return ToolContentWindow(
            text=stored.text[safe_offset:end],
            start_offset=safe_offset,
            end_offset=end,
            page=locator["page"],
            paragraph_title=locator["paragraph_title"],
            section_path=locator["section_path"],
            anchor_names=locator["anchor_names"],
        )

    async def _load_one(
            self,
            *,
            content_id: str,
            session_id: str,
    ) -> tuple[str, StoredToolContent] | ToolContentReadMatch:
        try:
            loaded = await self.load_stored_content(
                content_id=content_id,
                session_id=session_id,
            )
            if loaded is None:
                return ToolContentReadMatch(
                    content_id=content_id,
                    status="failed",
                    reason="content_not_found",
                )
            return loaded
        except Exception as exc:
            return ToolContentReadMatch(
                content_id=content_id,
                status="failed",
                reason=exc.__class__.__name__,
            )

    async def _read_ranked_expand_across_contents(
            self,
            *,
            stored_items: tuple[tuple[str, StoredToolContent], ...],
            request: ToolContentReadRequest,
    ) -> tuple[ToolContentReadMatch, ...]:
        query = (request.query or "").strip()

        candidates: list[RankCandidate] = []
        source_by_candidate_id: dict[str, tuple[str, StoredToolContent, int]] = {}
        for canonical_id, stored in stored_items:
            # 跨文档统一构建候选池，后续只做一次全局排序，保持真正的 cross-content rank 语义。
            candidate_chunks = self._select_chunks(stored, request.selector)
            for chunk in candidate_chunks:
                text = ToolContentWindowBuilder.chunk_text(stored, chunk)
                if not text:
                    continue
                candidate_id = f"{canonical_id}:chunk:{chunk.chunk_index}"
                source_by_candidate_id[candidate_id] = (canonical_id, stored, chunk.chunk_index)
                candidates.append(
                    RankCandidate(
                        candidate_id=candidate_id,
                        text=text,
                        fields={
                            "section": " / ".join(chunk.section_path),
                            "anchor": " ".join(chunk.anchor_names),
                        },
                        metadata={
                            "content_id": canonical_id,
                            "chunk_index": chunk.chunk_index,
                        },
                        group_key=canonical_id,
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

        return tuple(
            ToolContentReadMatch(
                content_id=source_by_candidate_id[item.candidate_id][0],
                status="success",
                window=ToolContentWindowBuilder.expand(
                    source_by_candidate_id[item.candidate_id][1],
                    center_chunk=source_by_candidate_id[item.candidate_id][2],
                    merge_before=request.merge_before,
                    merge_after=request.merge_after,
                ),
            )
            for item in ranked
        )

    def _read_regex_match_across_contents(
            self,
            *,
            stored_items: tuple[tuple[str, StoredToolContent], ...],
            request: ToolContentReadRequest,
    ) -> tuple[ToolContentReadMatch, ...]:
        pattern = request.pattern or ""
        regex = re.compile(pattern)

        matches: list[ToolContentReadMatch] = []
        try:
            for canonical_id, stored in stored_items:
                # regex_match 保持输入文档顺序扫描，但输出仍归一到同一个全局 matches 列表。
                candidate_chunks = self._select_chunks(stored, request.selector)
                seen_centers: set[int] = set()
                for chunk in candidate_chunks:
                    text = ToolContentWindowBuilder.chunk_text(stored, chunk)
                    for _ in regex.finditer(text):
                        if chunk.chunk_index in seen_centers:
                            continue
                        seen_centers.add(chunk.chunk_index)
                        matches.append(
                            ToolContentReadMatch(
                                content_id=canonical_id,
                                status="success",
                                window=ToolContentWindowBuilder.expand(
                                    stored,
                                    center_chunk=chunk.chunk_index,
                                    merge_before=request.merge_before,
                                    merge_after=request.merge_after,
                                ),
                            )
                        )
                        if len(matches) >= max(request.max_matches, 0):
                            raise _RegexLimitReached
        except _RegexLimitReached:
            pass

        return tuple(matches)

    def _select_chunks(
            self,
            stored: StoredToolContent,
            selector: ToolContentSelector | None,
    ) -> tuple[ToolContentChunk, ...]:
        chunks = tuple(sorted(stored.chunks, key=lambda c: c.chunk_index))
        if selector is None:
            return chunks

        selected: set[int] | None = None
        if selector.chunk_indices:
            selected = set(selector.chunk_indices)

        indexed = self._index_selected_chunks(stored, selector)
        if indexed is not None:
            selected = indexed if selected is None else selected & indexed

        if selected is None and selector.unit_types:
            selected = {
                c.chunk_index
                for c in chunks
                if set(selector.unit_types) & set(c.unit_types)
            }

        if selected is None:
            selected = {c.chunk_index for c in chunks}

        result = []
        for chunk in chunks:
            if chunk.chunk_index not in selected:
                continue
            if selector.unit_types and not selector.include_unknown and not chunk.unit_types:
                continue
            result.append(chunk)

        return tuple(result)

    def _index_selected_chunks(
            self,
            stored: StoredToolContent,
            selector: ToolContentSelector,
    ) -> set[int] | None:
        selected: set[int] | None = None

        for prefix, values in (
                ("section", selector.sections),
                ("page", selector.pages),
                ("anchor", selector.anchors),
        ):
            if not values:
                continue

            matched: set[int] = set()
            for entry in (stored.index.entries if stored.index else ()):
                name = entry.name
                bare_name = name.split(":", 1)[1] if ":" in name else name

                if any(v == name or v == bare_name or v in bare_name for v in values):
                    if name.startswith(f"{prefix}:") or prefix in name:
                        matched.update(entry.chunk_indices)

            selected = matched if selected is None else selected & matched

        return selected
