from __future__ import annotations

import re

from markdown_it import MarkdownIt

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

_MARKDOWN = MarkdownIt("commonmark")


class ToolContentInvalidRegexError(ValueError):
    """正则表达式语法无效。"""


class ToolContentReadService:
    """跨文档内容检索服务，支持两种模式：
    1. ranked_expand: 基于重排（Rerank）的语义相关性检索及上下文扩展
    2. regex_match:   基于正则表达式的精确文本匹配
    """

    __slots__ = ("_store", "_ranking_engine", "_window_builder")

    def __init__(
        self,
        *,
        store: ToolContentStore,
        ranking_engine: RankingEngine | None = None,
        max_window_chars: int | None = None,
    ) -> None:
        self._store = store
        self._ranking_engine = ranking_engine or get_ranking_engine(
            "read.ranked_expand"
        )
        self._window_builder = ToolContentWindowBuilder(max_window_chars=max_window_chars)

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
        """从存储层获取文档对象。"""
        stored = await self._store.get(content_id=content_id, session_id=session_id)
        if stored is None:
            return None

        return content_id, stored

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
            text=self._window_builder.truncate(stored.text[safe_offset:end]),
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
        chunks_by_content_id: dict[str, tuple[ToolContentChunk, ...]] = {}

        # 1. 提取并组装所有满足过滤条件的候选 Chunk
        for canonical_id, stored in stored_items:
            candidate_chunks = self._select_chunks(stored, request.selector)
            chunks_by_content_id[canonical_id] = candidate_chunks
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
                            "section": " / ".join(chunk.section_path),
                            "anchor": " ".join(chunk.anchor_labels),
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

        try:
            regex = re.compile(request.pattern)
        except re.error as exc:
            raise ToolContentInvalidRegexError(str(exc)) from exc

        matches: list[ToolContentReadMatch] = []
        seen_windows: set[tuple[str, int]] = set()
        for canonical_id, stored in stored_items:
            candidate_chunks = self._select_chunks(stored, request.selector)

            for chunk in candidate_chunks:
                text = ToolContentWindowBuilder.chunk_text(stored, chunk)

                if not _regex_matches(regex, text):
                    continue

                match_key = (canonical_id, chunk.chunk_index)
                if match_key in seen_windows:
                    continue
                seen_windows.add(match_key)

                matches.append(
                    ToolContentReadMatch(
                        content_id=canonical_id,
                        window=self._window_builder.expand(
                            stored,
                            chunks=candidate_chunks,
                            center_chunk=chunk.chunk_index,
                            merge_before=request.merge_before,
                            merge_after=request.merge_after,
                        ),
                    )
                )
                if len(matches) >= max_matches:
                    return tuple(matches)

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

        # 3. 按结构块类型过滤，多条件间取交集
        if selector.block_kinds:
            block_kinds = set(selector.block_kinds)
            block_selected = {
                c.chunk_index
                for c in chunks
                if block_kinds & set(c.block_kinds)
            }
            selected = (
                block_selected
                if selected is None
                else selected & block_selected
            )

        if selected is None:
            selected = {c.chunk_index for c in chunks}

        return tuple(chunk for chunk in chunks if chunk.chunk_index in selected)

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
    normalized_values = tuple(
        value.strip() for value in values if value and value.strip()
    )
    if entry.locator_kind == "page":
        locator_label = (
            entry.locator_name.removeprefix("page:")
            if entry.locator_name.startswith("page:")
            else entry.locator_name
        )
        # 页码必须按标签精确匹配，避免 page_labels=["4"] 误命中 page:14。
        page_values = (entry.page_label, locator_label)
        return any(
            target == candidate_text
            for target in normalized_values
            for candidate in page_values
            if (candidate_text := str(candidate or "").strip())
        )

    match_values = [entry.locator_name]
    if entry.locator_kind == "section":
        match_values.append(" > ".join(entry.section_path))
    elif entry.locator_kind == "anchor" and entry.anchor_label:
        match_values.append(entry.anchor_label)

    for target in normalized_values:
        for candidate in match_values:
            candidate_text = str(candidate).strip()
            if candidate_text and (
                target == candidate_text or target in candidate_text
            ):
                return True
    return False


def _regex_matches(regex: re.Pattern[str], text: str) -> bool:
    if regex.search(text) is not None:
        return True

    # PDF 解析后的 Markdown 源码可能把同一个标识符拆成 emphasis token。
    rendered_texts = tuple(
        dict.fromkeys(
            candidate
            for candidate in (
                _markdown_plain_text(text),
                _remove_markdown_word_markers(text),
            )
            if candidate and candidate != text
        )
    )
    for candidate_text in rendered_texts:
        if regex.search(candidate_text) is not None:
            return True

    markdown_pattern = _markdown_underscore_pattern(regex)
    if markdown_pattern is None:
        return False

    for candidate_text in rendered_texts:
        if markdown_pattern.search(candidate_text) is not None:
            return True
    return False


def _markdown_underscore_pattern(regex: re.Pattern[str]) -> re.Pattern[str] | None:
    relaxed_pattern = _relax_literal_underscores(regex.pattern)
    if relaxed_pattern == regex.pattern:
        return None

    try:
        return re.compile(relaxed_pattern, regex.flags)
    except re.error:
        return None


def _markdown_plain_text(text: str) -> str:
    try:
        tokens = _MARKDOWN.parse(text)
    except Exception:
        return text

    parts: list[str] = []
    _append_markdown_token_text(tokens, parts)
    return "".join(parts)


def _append_markdown_token_text(tokens, parts: list[str]) -> None:
    for token in tokens:
        if token.type in {"text", "code_inline", "code_block", "fence"}:
            parts.append(token.content)
        elif token.type in {"softbreak", "hardbreak"}:
            parts.append("\n")
        elif token.children:
            _append_markdown_token_text(token.children, parts)


def _remove_markdown_word_markers(text: str) -> str:
    return re.sub(r"(?<=\w)[*_]+(?=\w)", "", text)


def _relax_literal_underscores(pattern: str) -> str:
    parts: list[str] = []
    escaped = False
    in_class = False

    for char in pattern:
        if escaped:
            parts.append(char)
            escaped = False
            continue

        if char == "\\":
            parts.append(char)
            escaped = True
            continue

        if char == "[":
            in_class = True
            parts.append(char)
            continue
        if char == "]" and in_class:
            in_class = False
            parts.append(char)
            continue

        if char == "_" and not in_class:
            parts.append(r"[_\s]+")
            continue

        parts.append(char)

    return "".join(parts)
