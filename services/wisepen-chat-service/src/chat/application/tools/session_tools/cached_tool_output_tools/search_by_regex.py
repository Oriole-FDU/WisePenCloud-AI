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

_MAX_REGEX_CHARS = 500    # 正则表达式的最大字符限制
_DEFAULT_MAX_MATCHES = 10    # 默认最大匹配数(正则匹配数，非窗口数)
_MAX_MATCHES = 100    # 最大匹配数上限
_REGEX_CONTEXT_CHARS = 200    # 单边拓展上下文上限
_REGEX_CLUSTER_GAP_CHARS = _REGEX_CONTEXT_CHARS * 2    # 窗口融合的gap上限
_REGEX_SENTENCE_BOUNDARIES = frozenset(".。!?！？;；\n")    # sentence分隔符集合
_SEARCH_TIMEOUT_SECONDS = 5    # regex最大搜索时间，避免复杂正则搜索超时
_TIMEOUT_SECONDS = 300.0

_PARAMETERS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "content_ids": {
            "type": "array",
            "items": {"type": "string", "minLength": 1},
            "minItems": 1,
            "maxItems": 16,
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
            "description": "Maximum exact regex matches collected across all cached tool outputs; at most 100.",
        },
    },
    "required": ["content_ids", "pattern"],
    "additionalProperties": False,
}


@dataclass(slots=True)
class CachedToolOutputRegexHighlight:
    """窗口内一个正则命中的可见高光。"""

    text: str
    # 相对所属 window.text 的 Python 字符半开区间，不是全局原文偏移。
    start_offset: int
    end_offset: int


@dataclass(slots=True)
class CachedToolOutputRegexWindow:
    """一个命中簇的连续上下文窗口及其窗口内高光。"""

    content_id: str
    window: CachedToolOutputWindow
    highlights: list[CachedToolOutputRegexHighlight]


@dataclass(slots=True)
class CachedToolOutputSearchByRegexResult:
    """正则搜索结果；命中数和最终聚类窗口数使用不同计数。"""

    matched_count: int  # 实际收集的精确匹配数，受 max_matches 限制。
    window_count: int  # 按位置聚类并合并后的最终窗口数。
    windows: list[CachedToolOutputRegexWindow] = field(default_factory=list)

class CachedToolOutputSearchByRegexTool:

    def __init__(self) -> None:
        self._definition = ToolDefinition(
            llm_spec=ToolLLMSpec(
                name="search_cached_tool_output_by_regex",
                description = (
                    "Search complete cached source texts with a Python regular expression. "
                    "Best for exact literal names, codes, identifiers, citations, URLs, or patterns that may span chunk boundaries.\n\n"
                    "Key Behaviors:\n"
                    "- max_matches limits total exact regex hits, which are then clustered into compact context windows.\n"
                    "- Windows provide absolute start_offset and end_offset for follow-up reads with read_cached_tool_output_by_range.\n"
                    "- Highlight offsets are relative to each returned window's text.\n\n"
                    "For meaning-based or conceptual queries, use search_cached_tool_output_by_semantics instead."
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
    ) -> CachedToolOutputSearchByRegexResult:
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
            result = await _search_by_regex(
                stored_items=stored_items,
                pattern=pattern,
                max_matches=kwargs["max_matches"],
            )
            return result
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
) -> CachedToolOutputSearchByRegexResult:
    def scan_loaded() -> CachedToolOutputSearchByRegexResult:
        # regex 搜索放到工作线程里执行，避免复杂表达式阻塞事件循环。
        compiled = regex.compile(pattern)
        matched_ranges: list[tuple[StoredCachedToolOutput, int, int]] = []    # (stored, matched.start, matched.end)
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
        windows = _build_regex_windows(
            matched_ranges=matched_ranges,
        )
        return CachedToolOutputSearchByRegexResult(
            matched_count=len(matched_ranges),
            window_count=len(windows),
            windows=windows,
        )

    return await asyncio.to_thread(scan_loaded)


def _build_regex_windows(
    *,
    matched_ranges: Sequence[tuple[StoredCachedToolOutput, int, int]],
) -> list[CachedToolOutputRegexWindow]:
    """按正文分别聚类命中，并把每个命中簇投影为一个短窗口。

    正则匹配先以全局原文坐标保存；窗口生成完成后才将匹配坐标转换为窗口内坐标，
    从而同时保留精确高光和 range 工具所需的全局窗口范围。
    """

    windows: list[CachedToolOutputRegexWindow] = []
    # 正文对象去重
    for stored in _ordered_stored_items(matched_ranges):
        ranges = [
            (match_start, match_end)
            for item, match_start, match_end in matched_ranges
            if item is stored
        ]
        for cluster in _cluster_match_ranges(ranges):
            window_start, window_end = _regex_window_range(
                stored.text,
                match_start=cluster[0][0],
                match_end=cluster[-1][1],
            )
            window = CachedToolOutputWindow(
                text=stored.text[window_start:window_end],
                start_offset=window_start,
                end_offset=window_end,
                # 本工具没有总窗口预算；未找到句界时按字符边界自然结束。
                truncated=False,
            )
            windows.append(
                CachedToolOutputRegexWindow(
                    content_id=stored.content_id,
                    window=window,
                    highlights=[
                        CachedToolOutputRegexHighlight(
                            text=stored.text[match_start:match_end],
                            start_offset=match_start - window_start,
                            end_offset=match_end - window_start,
                        )
                        for match_start, match_end in cluster
                    ],
                )
            )
    return windows


def _ordered_stored_items(
    matched_ranges: Sequence[tuple[StoredCachedToolOutput, int, int]],
) -> list[StoredCachedToolOutput]:
    """同一个正文对象有可能被多次匹配，此处按照首次命中的顺序去重"""
    seen_ids: set[int] = set()
    stored_items: list[StoredCachedToolOutput] = []

    for stored, _, _ in matched_ranges:
        obj_id = id(stored)
        if obj_id not in seen_ids:
            seen_ids.add(obj_id)
            stored_items.append(stored)

    return stored_items


def _cluster_match_ranges(
    ranges: Sequence[tuple[int, int]],
) -> list[list[tuple[int, int]]]:
    """把相邻或重叠的正则命中合并为连续簇。"""

    ordered_ranges = sorted(ranges)
    if not ordered_ranges:
        return []

    clusters: list[list[tuple[int, int]]] = []
    current = [ordered_ranges[0]]
    current_end = ordered_ranges[0][1]
    for match_start, match_end in ordered_ranges[1:]:
        if match_start - current_end <= _REGEX_CLUSTER_GAP_CHARS:
            current.append((match_start, match_end))
            current_end = max(current_end, match_end)
            continue
        clusters.append(current)
        current = [(match_start, match_end)]
        current_end = match_end
    clusters.append(current)
    return clusters


def _regex_window_range(
    text: str,
    *,
    match_start: int,
    match_end: int,
) -> tuple[int, int]:
    """在命中簇两侧各取最多 200 字符，并优先落在最近句界。"""
    # 确认大候选边界边界
    candidate_start = max(match_start - _REGEX_CONTEXT_CHARS, 0)
    candidate_end = min(match_end + _REGEX_CONTEXT_CHARS, len(text))

    # 回退边界，从最佳标点处截断，避免窗口内出现半截语句
    left_boundary = max(
        (
            index + 1
            for index in range(candidate_start, match_start)
            if text[index] in _REGEX_SENTENCE_BOUNDARIES
        ),
        default=candidate_start,
    )
    right_boundary = next(
        (
            index + 1
            for index in range(match_end, candidate_end)
            if text[index] in _REGEX_SENTENCE_BOUNDARIES
        ),
        candidate_end,
    )
    return left_boundary, right_boundary


def _policy() -> ToolPolicy:
    return ToolPolicy(
        expose_by_default=False,
        selection_mode=ToolSelectionMode.CONTEXTUAL,
        persist_output=True,
        risk_level=ToolRiskLevel.LOW,
        required_context_keys=("session_id",),
        timeout_seconds=_TIMEOUT_SECONDS,
    )
