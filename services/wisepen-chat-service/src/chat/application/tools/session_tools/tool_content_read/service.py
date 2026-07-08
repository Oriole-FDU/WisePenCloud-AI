from __future__ import annotations

import re

from chat.application.tools.common.tool_content_store import ToolContentStore
from chat.application.tools.common.tool_content_store.core.models import (
    StoredToolContent,
    ToolContentChunk,
)
from chat.application.tools.session_tools.tool_content_read.content_window_builder import (
    ToolContentWindowBuilder,
)
from chat.application.tools.session_tools.tool_content_read.models import (
    ToolContentReadMatch,
    ToolContentReadResult,
    ToolContentRegexReadRequest,
    ToolContentRerankReadRequest,
    ToolContentSelector,
    ToolContentWindow,
)
from chat.application.utils.ranking_engine.engine import RankingEngine
from chat.application.utils.ranking_engine.models import (
    RankCandidate,
    RankQuery,
    RankRequest,
)
from chat.application.utils.ranking_engine.registry import get_ranking_engine


class _RegexLimitReached(Exception):
    """异常：用于在正则匹配达到 max_matches 上限时快速跳出嵌套循环。"""

    pass


class ToolContentReadService:
    """跨文档内容检索服务，支持两种模式：
    1. ranked_expand: 基于重排（Rerank）的语义相关性检索及上下文扩展
    2. regex_match:   基于正则表达式的精确文本匹配
    """

    __slots__ = ("_store", "_ranking_engine")

    def __init__(
        self,
        *,
        store: ToolContentStore,
        ranking_engine: RankingEngine | None = None,
    ) -> None:
        self._store = store
        self._ranking_engine = ranking_engine or get_ranking_engine(
            "read.ranked_expand"
        )

    async def read_ranked_expand(
        self,
        *,
        request: ToolContentRerankReadRequest,
        session_id: str,
    ) -> ToolContentReadResult:
        """跨文档语义检索并展开窗口。"""
        stored_items, failed = await self._load_contents(
            content_ids=request.content_ids,
            session_id=session_id,
        )
        matches = await self._read_ranked_expand_across_contents(
            stored_items=stored_items,
            request=request,
        )

        return ToolContentReadResult(
            matches=matches,
            failed=failed,
        )

    async def read_regex_match(
        self,
        *,
        request: ToolContentRegexReadRequest,
        session_id: str,
    ) -> ToolContentReadResult:
        """跨文档正则匹配并展开窗口。"""
        stored_items, failed = await self._load_contents(
            content_ids=request.content_ids,
            session_id=session_id,
        )
        matches = self._read_regex_match_across_contents(
            stored_items=stored_items,
            request=request,
        )

        return ToolContentReadResult(
            matches=matches,
            failed=failed,
        )

    async def load_stored_content(
        self,
        *,
        content_id: str,
        session_id: str,
    ) -> tuple[str, StoredToolContent] | None:
        """从存储层获取规范化后的文档对象"""
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
        """根据指定的字符偏移量和长度，切出一段连续的文本窗口并补全元数据"""
        safe_offset = max(offset, 0)
        safe_limit = max(limit, 0)
        end = min(len(stored.text), safe_offset + safe_limit)

        # 筛选覆盖当前窗口范围的所有原始 chunk
        chunks = tuple(
            chunk
            for chunk in stored.chunks
            if chunk.start_offset is not None
            and chunk.end_offset is not None
            and chunk.start_offset < end
            and chunk.end_offset > safe_offset
        )
        # 借用 builder 定位窗口对应的页码、章节和锚点
        locator = ToolContentWindowBuilder.locator(stored, chunks)

        return ToolContentWindow(
            text=stored.text[safe_offset:end],
            start_offset=safe_offset,
            end_offset=end,
            page_label=locator["page_label"],
            section_title=locator["section_title"],
            section_path=locator["section_path"],
            anchor_labels=locator["anchor_labels"],
        )

    async def _load_contents(
        self,
        *,
        content_ids: tuple[str, ...],
        session_id: str,
    ) -> tuple[
        tuple[tuple[str, StoredToolContent], ...], tuple[ToolContentReadMatch, ...]
    ]:
        stored_items: list[tuple[str, StoredToolContent]] = []
        failed: list[ToolContentReadMatch] = []

        for content_id in content_ids:
            try:
                loaded = await self.load_stored_content(
                    content_id=content_id,
                    session_id=session_id,
                )
            except Exception as exc:
                failed.append(
                    ToolContentReadMatch(
                        content_id=content_id,
                        reason=exc.__class__.__name__,
                    )
                )
                continue

            if loaded is None:
                failed.append(
                    ToolContentReadMatch(
                        content_id=content_id,
                        reason="content_not_found",
                    )
                )
                continue
            stored_items.append(loaded)

        return tuple(stored_items), tuple(failed)

    async def _read_ranked_expand_across_contents(
        self,
        *,
        stored_items: tuple[tuple[str, StoredToolContent], ...],
        request: ToolContentRerankReadRequest,
    ) -> tuple[ToolContentReadMatch, ...]:
        """跨文档语义检索：聚合多文档 Chunk 后调用重排引擎，并对 Top-K 结果进行上下文扩展"""
        query = request.query.strip()

        candidates: list[RankCandidate] = []
        source_by_candidate_id: dict[str, tuple[str, StoredToolContent, int]] = {}

        # 1. 提取并组装所有满足过滤条件的候选 Chunk
        for canonical_id, stored in stored_items:
            candidate_chunks = self._select_chunks(stored, request.selector)
            for chunk in candidate_chunks:
                text = ToolContentWindowBuilder.chunk_text(stored, chunk)
                if not text:
                    continue

                candidate_id = f"{canonical_id}:chunk:{chunk.chunk_index}"
                source_by_candidate_id[candidate_id] = (
                    canonical_id,
                    stored,
                    chunk.chunk_index,
                )

                candidates.append(
                    RankCandidate(
                        candidate_id=candidate_id,
                        text=text,
                        fields={
                            "section_path_text": " / ".join(chunk.section_path),
                            "anchor_labels_text": " ".join(chunk.anchor_labels),
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

        # 2. 调用重排引擎排序
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

        # 3. 构造扩展后的文本窗口结果
        return tuple(
            ToolContentReadMatch(
                content_id=source_by_candidate_id[item.candidate_id][0],
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
        request: ToolContentRegexReadRequest,
    ) -> tuple[ToolContentReadMatch, ...]:
        """跨文档正则匹配：线性扫描文本，命中后执行窗口扩展，支持最大匹配数熔断"""
        max_matches = max(request.max_matches, 0)
        if max_matches == 0:
            return ()

        regex = re.compile(request.pattern)
        matches: list[ToolContentReadMatch] = []
        try:
            for canonical_id, stored in stored_items:
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
                                window=ToolContentWindowBuilder.expand(
                                    stored,
                                    center_chunk=chunk.chunk_index,
                                    merge_before=request.merge_before,
                                    merge_after=request.merge_after,
                                ),
                            )
                        )
                        # 达到全局最大匹配上限时触发熔断
                        if len(matches) >= max_matches:
                            raise _RegexLimitReached
        except _RegexLimitReached:
            pass

        return tuple(matches)

    def _select_chunks(
        self,
        stored: StoredToolContent,
        selector: ToolContentSelector | None,
    ) -> tuple[ToolContentChunk, ...]:
        """Chunk 过滤器：基于 selector（索引、ID、单元类型等）多级筛选 Chunk 集合"""
        chunks = tuple(sorted(stored.chunks, key=lambda c: c.chunk_index))
        if selector is None:
            return chunks

        selected: set[int] | None = None

        # 1. 按指定的 chunk_indices 过滤
        if selector.chunk_indices:
            selected = set(selector.chunk_indices)

        # 2. 按倒排索引（章节、页码、锚点）过滤，多条件间取交集
        indexed = self._index_selected_chunks(stored, selector)
        if indexed is not None:
            selected = indexed if selected is None else selected & indexed

        # 3. 按结构块类型过滤
        if selected is None and selector.block_kinds:
            selected = {
                c.chunk_index
                for c in chunks
                if set(selector.block_kinds) & set(c.block_kinds)
            }

        if selected is None:
            selected = {c.chunk_index for c in chunks}

        # 4. 后置清洗：处理未知类型的白名单控制
        result = []
        for chunk in chunks:
            if chunk.chunk_index not in selected:
                continue
            if (
                selector.block_kinds
                and not selector.include_unknown
                and not chunk.block_kinds
            ):
                continue
            result.append(chunk)

        return tuple(result)

    def _index_selected_chunks(
        self,
        stored: StoredToolContent,
        selector: ToolContentSelector,
    ) -> set[int] | None:
        """通过解析文档自带的索引实体，快速提取匹配属性的 chunk_index 集合"""
        selected: set[int] | None = None

        for prefix, values in (
            ("section", selector.sections),
            ("page", selector.page_labels),
            ("anchor", selector.anchor_labels),
        ):
            if not values:
                continue

            matched: set[int] = set()
            for entry in stored.index.entries if stored.index else ():
                if entry.locator_kind != prefix:
                    continue
                if _matches_selector_value(entry, values):
                    matched.update(entry.chunk_indices)

            selected = matched if selected is None else selected & matched

        return selected


def _matches_selector_value(entry, values: tuple[str, ...]) -> bool:
    locator_name = entry.locator_name
    match_values = [locator_name]

    if entry.locator_kind == "section":
        match_values.append(" > ".join(entry.section_path))
    elif entry.locator_kind == "page" and entry.page_label:
        match_values.append(entry.page_label)
    elif entry.locator_kind == "anchor" and entry.anchor_label:
        match_values.append(entry.anchor_label)

    normalized_values = tuple(
        value.strip() for value in values if value and value.strip()
    )
    for target in normalized_values:
        for candidate in match_values:
            candidate_text = str(candidate).strip()
            if candidate_text and (
                target == candidate_text or target in candidate_text
            ):
                return True
    return False
