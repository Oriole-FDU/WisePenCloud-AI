from __future__ import annotations

import asyncio
from collections.abc import Iterable

import regex
from chat.application.tools.common.tool_content_store import StoredToolContent
from markdown_it import MarkdownIt
from markdown_it.token import Token

from ..content_loader import ToolContentLoader
from ..content_window_builder import ToolContentWindowBuilder, chunk_text
from ..models import (
    ToolContentRegexMatch,
    ToolContentRegexReadRequest,
    ToolContentRegexReadResult,
)
from ._utils.chunk_selection import select_chunks

_MARKDOWN = MarkdownIt("commonmark")
_MALFORMED_EMPHASIZED_IDENTIFIER_RE = regex.compile(
    r"(?<!\w)[_*](?P<head>[\p{L}\p{N}]+)[*_](?P<tail>[\p{L}\p{N}]+)"
)
_EMPHASIZED_IDENTIFIER_PART_RE = regex.compile(
    r"(?<!\w)(?P<marker>[_*])(?P<part>[\p{L}\p{N}])(?P=marker)[ \t]+"
    r"(?=[\p{L}\p{N}])"
)
_TEXT_TOKEN_TYPES = {"text", "code_inline", "code_block", "fence"}
_BREAK_TOKEN_TYPES = {"softbreak", "hardbreak"}
_SEARCH_TIMEOUT_SECONDS = 0.05


class ToolContentInvalidRegexError(ValueError):
    """正则表达式语法无效。"""


class ToolContentRegexTimeoutError(TimeoutError):
    """单次正则搜索超过执行时间上限。"""


class RegexMatchReader:
    """按 chunk 扫描正则命中，并构造相邻扩展窗口。"""

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
    ) -> ToolContentRegexReadResult:
        stored_items, failed = await self._loader.load_many(
            content_ids=request.content_ids,
            session_id=session_id,
        )
        matches = await asyncio.to_thread(self._read_loaded, stored_items, request)
        return ToolContentRegexReadResult(matches=matches, failed=failed)

    def _read_loaded(
            self,
            stored_items: tuple[tuple[str, StoredToolContent], ...],
            request: ToolContentRegexReadRequest,
    ) -> tuple[ToolContentRegexMatch, ...]:
        try:
            compiled = regex.compile(request.pattern)
        except regex.error as exc:
            raise ToolContentInvalidRegexError(str(exc)) from exc

        max_matches = max(request.max_matches, 0)
        if max_matches == 0:
            return ()

        matches: list[ToolContentRegexMatch] = []
        for content_id, stored in stored_items:
            chunks = select_chunks(stored, request.selector)
            for chunk in chunks:
                text = chunk_text(stored, chunk)
                try:
                    matched = any(
                        compiled.search(view, timeout=_SEARCH_TIMEOUT_SECONDS)
                        for view in _markdown_search_views(text)
                    )
                except TimeoutError as exc:
                    raise ToolContentRegexTimeoutError(
                        f"regex search exceeded {_SEARCH_TIMEOUT_SECONDS} seconds"
                    ) from exc
                if not matched:
                    continue

                matches.append(
                    ToolContentRegexMatch(
                        content_id=content_id,
                        window=self._window_builder.build_expanded_window(
                            stored,
                            chunks=chunks,
                            center_chunk=chunk.chunk_index,
                            merge_before=request.merge_before,
                            merge_after=request.merge_after,
                        ),
                    )
                )
                if len(matches) >= max_matches:
                    return tuple(matches)

        return tuple(matches)


def _markdown_search_views(text: str) -> tuple[str, ...]:
    """生成有限的 Markdown 等价文本视图，且不改写调用方的正则语义。"""
    rendered = _markdown_plain_text(text)
    views = [text, rendered]

    for source in tuple(views):
        views.append(
            _MALFORMED_EMPHASIZED_IDENTIFIER_RE.sub(
                lambda match: f"{match.group('head')}{match.group('tail')}",
                source,
            )
        )
        views.append(
            _EMPHASIZED_IDENTIFIER_PART_RE.sub(
                lambda match: f"{match.group('part')}_",
                source,
            )
        )

    return tuple(dict.fromkeys(view for view in views if view))


def _markdown_plain_text(text: str) -> str:
    try:
        tokens = _MARKDOWN.parse(text)
    except Exception:
        return text

    parts: list[str] = []
    for token in _flatten_tokens(tokens):
        if token.type in _TEXT_TOKEN_TYPES:
            parts.append(token.content)
        elif token.type in _BREAK_TOKEN_TYPES:
            parts.append("\n")
    return "".join(parts)


def _flatten_tokens(tokens: Iterable[Token]) -> Iterable[Token]:
    for token in tokens:
        yield token
        if token.children:
            yield from _flatten_tokens(token.children)
