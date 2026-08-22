from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

import regex

from chat.application.tools.core import (
    ToolDefinition,
    ToolExecutionError,
    ToolLLMSpec,
    ToolParametersSchema,
    ToolPolicy,
    ToolRiskLevel,
    ToolSelectionMode,
    ToolUISpec,
)
from chat.application.tools.core.output_cache.cache_store import (
    StoredToolContent as StoredCachedToolOutput,
)
from chat.application.tools.core.output_cache.cache_store import (
    get_tool_content,
)
from chat.application.tools.session_tools.cached_tool_output_tools.window import (
    CachedToolOutputWindow,
)

_MAX_REGEX_CHARS = 500
_DEFAULT_MAX_MATCHES = 100
_MAX_MATCHES = 100
_DEFAULT_EXPAND_COUNT = 10
_MAX_EXPAND_COUNT = 10
_REGEX_CONTEXT_CHARS = 800
_SEARCH_TIMEOUT_SECONDS = 5
_TIMEOUT_SECONDS = 300.0

_PARAMETERS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "content_ids": {
            "type": "array",
            "items": {"type": "string", "minLength": 1},
            "minItems": 1,
            "maxItems": 64,
            "description": "One or more cached tool output content_id values returned in previous tool results.",
        },
        "pattern": {
            "type": "string",
            "minLength": 1,
            "maxLength": _MAX_REGEX_CHARS,
            "description": "Python regular expression matched against the complete stored source text.",
        },
        "max_matches": {
            "type": "integer",
            "default": _DEFAULT_MAX_MATCHES,
            "minimum": 1,
            "maximum": _MAX_MATCHES,
            "description": (
                "Maximum regex matches scanned across all cached tool outputs; this is independent "
                "from how many match windows are expanded."
            ),
        },
        "match_offset": {
            "type": "integer",
            "minimum": 0,
            "default": 0,
            "description": "Zero-based global match index from which window expansion starts.",
        },
        "expand_count": {
            "type": "integer",
            "default": _DEFAULT_EXPAND_COUNT,
            "minimum": 1,
            "maximum": _MAX_EXPAND_COUNT,
            "description": (
                "Number of matched occurrences to expand in this call; at most 10. "
                "This is independent from max_matches."
            ),
        },
    },
    "required": ["content_ids", "pattern"],
    "additionalProperties": False,
}


@dataclass(slots=True)
class CachedToolOutputSearchByRegexMatch:
    content_id: str
    match_start: int
    match_end: int
    window: CachedToolOutputWindow


class CachedToolOutputSearchByRegexTool:

    def __init__(self) -> None:
        self._definition = ToolDefinition(
            llm_spec=ToolLLMSpec(
                name="search_cached_tool_output_by_regex",
                description=(
                    "Search complete cached source texts with a Python regular expression. "
                    "Use this for exact names, identifiers, citations, headings, URLs, or other "
                    "literal patterns, including matches that cross retrieval chunk boundaries. "
                    "Results include absolute match offsets and bounded source context. "
                    "Each expanded match includes 800 source characters before and after it when available. "
                    "Use match_offset and expand_count to page through matches; expand_count is capped at 10 "
                    "and is independent from max_matches. "
                    "Use search_cached_tool_output_by_semantics for meaning-based retrieval. Use read tools "
                    "after you know the desired range, pages, or sections."
                ),
                parameters_schema=ToolParametersSchema(_PARAMETERS_SCHEMA),
            ),
            policy=_policy(),
            ui_spec=ToolUISpec(
                display_name="正则搜索缓存的工具输出",
                description="在缓存工具输出全文中用正则表达式查找精确文本、编号、链接、标题或其他固定模式。",
            ),
        )

    @property
    def definition(self) -> ToolDefinition:
        return self._definition

    async def execute(
        self,
        context: dict[str, Any],
        config: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> list[CachedToolOutputSearchByRegexMatch]:
        del config
        # schema 已保证 pattern 非空；这里只负责执行正则语法检查。
        pattern = kwargs["pattern"]
        if len(pattern) > _MAX_REGEX_CHARS:
            # 限制正则长度，避免模型生成过长表达式导致匹配成本失控。
            raise ToolExecutionError(
                reason="regex_pattern_too_long",
                detail_reason=f"regex pattern is too long; max {_MAX_REGEX_CHARS} chars.",
            )
        try:
            # 预编译用于提前暴露语法错误，避免进入批量读取后才失败。
            regex.compile(pattern)
        except regex.error as exc:
            raise ToolExecutionError(
                reason="invalid_regex_pattern",
                detail_reason=str(exc),
            ) from exc

        try:
            # 搜索工具直接按 content_id 逐个读取缓存；不存在的 content 不参与搜索。
            stored_items = []
            session_id = context["session_id"]
            for content_id in kwargs["content_ids"]:
                stored = await get_tool_content(
                    content_id=content_id,
                    session_id=session_id,
                )
                if stored is not None:
                    stored_items.append(stored)
            return await _search_by_regex(
                stored_items=stored_items,
                pattern=pattern,
                max_matches=kwargs["max_matches"],
                match_offset=kwargs["match_offset"],
                expand_count=kwargs["expand_count"],
            )
        except Exception as exc:
            raise ToolExecutionError(
                reason="search_cached_tool_output_by_regex_failed",
                detail_reason=str(exc),
                retryable=False,
            ) from exc


async def _search_by_regex(
    *,
    stored_items: Sequence[StoredCachedToolOutput],
    pattern: str,
    max_matches: int,
    match_offset: int,
    expand_count: int,
) -> list[CachedToolOutputSearchByRegexMatch]:
    def scan_loaded() -> list[CachedToolOutputSearchByRegexMatch]:
        # regex 搜索放到工作线程里执行，避免复杂表达式阻塞事件循环。
        compiled = regex.compile(pattern)
        matched_ranges: list[tuple[StoredCachedToolOutput, int, int]] = []
        for stored in stored_items:
            try:
                # timeout 由 regex 库在单次扫描中控制，复杂表达式会转成工具错误返回。
                for matched in compiled.finditer(
                    stored.text,
                    timeout=_SEARCH_TIMEOUT_SECONDS,
                ):
                    matched_ranges.append((stored, matched.start(), matched.end()))
                    if len(matched_ranges) >= max_matches:
                        break
                if len(matched_ranges) >= max_matches:
                    break
            except TimeoutError as exc:
                raise TimeoutError(
                    f"regex search exceeded {_SEARCH_TIMEOUT_SECONDS}s"
                ) from exc
        selected_ranges = matched_ranges[match_offset : match_offset + expand_count]
        matches: list[CachedToolOutputSearchByRegexMatch] = []
        for stored, match_start, match_end in selected_ranges:
            window_start, window_end = _regex_window_range(
                stored.text,
                match_start=match_start,
                match_end=match_end,
            )
            # 固定上下文直接构造窗口；搜索结果本身不再引入额外字符预算。
            window = CachedToolOutputWindow(
                text=stored.text[window_start:window_end],
                start_offset=window_start,
                end_offset=window_end,
                truncated=False,
            )
            matches.append(
                CachedToolOutputSearchByRegexMatch(
                    content_id=stored.content_id,
                    match_start=match_start,
                    match_end=match_end,
                    window=window,
                )
            )
        return matches

    return await asyncio.to_thread(scan_loaded)


def _regex_window_range(
    text: str,
    *,
    match_start: int,
    match_end: int,
) -> tuple[int, int]:
    # 每个命中窗口固定取前后各 800 字符，正文边界不足时自然收缩。
    return (
        max(match_start - _REGEX_CONTEXT_CHARS, 0),
        min(match_end + _REGEX_CONTEXT_CHARS, len(text)),
    )


def _policy() -> ToolPolicy:
    return ToolPolicy(
        expose_by_default=False,
        selection_mode=ToolSelectionMode.CONTEXTUAL,
        persist_output=True,
        risk_level=ToolRiskLevel.LOW,
        required_context_keys=("session_id",),
        timeout_seconds=_TIMEOUT_SECONDS,
    )
