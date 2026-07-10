from __future__ import annotations

import re

from markdown_it import MarkdownIt

from chat.application.tools.common.tool_content_store.core.models import (
    StoredToolContent,
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
    ToolContentRegexReadRequest,
)
from chat.application.tools.session_tools.tool_content_read.chunk_selector import (
    select_chunks,
)

_MARKDOWN = MarkdownIt("commonmark")


class ToolContentInvalidRegexError(ValueError):
    """正则表达式语法无效。"""


class RegexMatchReader:
    """跨文档正则 reader：按 chunk 扫描，命中后构造扩展窗口。"""

    __slots__ = ("_loader", "_window_builder")

    def __init__(
        self,
        *,
        loader: ToolContentLoader,
        window_builder: ToolContentWindowBuilder,
    ) -> None:
        self._loader = loader
        self._window_builder = window_builder

    async def read(
        self,
        *,
        request: ToolContentRegexReadRequest,
        session_id: str,
    ) -> ToolContentReadResult:
        stored_items, failed = await self._loader.load_many(
            content_ids=request.content_ids,
            session_id=session_id,
        )
        return ToolContentReadResult(
            matches=self._read_loaded(
                stored_items=stored_items,
                request=request,
            ),
            failed=failed,
        )

    def _read_loaded(
        self,
        *,
        stored_items: tuple[tuple[str, StoredToolContent], ...],
        request: ToolContentRegexReadRequest,
    ) -> tuple[ToolContentReadMatch, ...]:
        max_matches = max(request.max_matches, 0)
        if max_matches == 0:
            return ()

        try:
            regex = re.compile(request.pattern)
        except re.error as exc:
            raise ToolContentInvalidRegexError(str(exc)) from exc

        matches: list[ToolContentReadMatch] = []
        seen_windows: set[tuple[str, int]] = set()
        for content_id, stored in stored_items:
            candidate_chunks = select_chunks(stored, request.selector)
            for chunk in candidate_chunks:
                text = ToolContentWindowBuilder.chunk_text(stored, chunk)
                if not _regex_matches(regex, text):
                    continue

                match_key = (content_id, chunk.chunk_index)
                if match_key in seen_windows:
                    continue
                seen_windows.add(match_key)

                matches.append(
                    ToolContentReadMatch(
                        content_id=content_id,
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


def _regex_matches(regex: re.Pattern[str], text: str) -> bool:
    """在原始 Markdown 和几个等价文本视图上匹配同一个正则。

    PDF/Docling/PyMuPDF 导出的 Markdown 会把视觉上连续的标识符拆开，例如
    `_d_ model`、`_d_model_`、`_BRCA_1`。用户通常按渲染后的文本写正则，
    所以这里先匹配原文，再按成本从低到高尝试 Markdown 兼容视图。
    """
    if regex.search(text) is not None:
        return True

    text_variants = _markdown_text_variants(text)
    for candidate_text in text_variants:
        if regex.search(candidate_text) is not None:
            return True

    markdown_pattern = _markdown_underscore_pattern(regex)
    if markdown_pattern is None:
        return False

    # 用户正则里写下划线时，Markdown 源文可能把它表现为强调边界或空白；
    # 只对字面量 "_" 放宽，字符类和转义内容保持原正则语义。
    for candidate_text in text_variants:
        if markdown_pattern.search(candidate_text) is not None:
            return True
    return False


def _markdown_text_variants(text: str) -> tuple[str, ...]:
    """生成与 Markdown 源文等价的文本视图，供同一个用户正则复用。

    - markdown-it 渲染视图：覆盖 `_d_ model` → `d model` 这类 emphasis 拆词。
    - word-marker 清理视图：覆盖 `_BRCA_1` → `BRCA1` 这类词内强调符号。
    """
    return tuple(
        dict.fromkeys(
            candidate
            for candidate in (
                _markdown_plain_text(text),
                _remove_markdown_word_markers(text),
            )
            if candidate and candidate != text
        )
    )


def _markdown_underscore_pattern(regex: re.Pattern[str]) -> re.Pattern[str] | None:
    """将正则中的字面量下划线放宽为 Markdown 源文常见的下划线/空白组合。"""
    relaxed_pattern = _relax_literal_underscores(regex.pattern)
    if relaxed_pattern == regex.pattern:
        return None

    try:
        return re.compile(relaxed_pattern, regex.flags)
    except re.error:
        return None


def _markdown_plain_text(text: str) -> str:
    """用 Markdown token 提取近似渲染文本，避免直接正则误读强调标记。"""
    try:
        tokens = _MARKDOWN.parse(text)
    except Exception:
        return text

    parts: list[str] = []
    for token in _flatten_markdown_tokens(tokens):
        if token.type in {"text", "code_inline", "code_block", "fence"}:
            parts.append(token.content)
        elif token.type in {"softbreak", "hardbreak"}:
            parts.append("\n")
    return "".join(parts)


def _flatten_markdown_tokens(tokens):
    for token in tokens:
        yield token
        if token.children:
            yield from _flatten_markdown_tokens(token.children)


def _remove_markdown_word_markers(text: str) -> str:
    """去掉词内强调符号；只处理字母数字之间的 * / _，避免破坏普通 Markdown。"""
    return re.sub(r"(?<=\w)[*_]+(?=\w)", "", text)


def _relax_literal_underscores(pattern: str) -> str:
    """只改写正则源码中的字面量 "_"，跳过转义字符和字符类。"""
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

        parts.append(r"[_\s]+" if char == "_" and not in_class else char)

    return "".join(parts)
