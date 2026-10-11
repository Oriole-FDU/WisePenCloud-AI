from __future__ import annotations

import asyncio
from bisect import bisect_right
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import regex
from blingfire import text_to_sentences_and_offsets

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
    StoredToolContent,
    get_tool_content,
)

_MAX_REGEX_CHARS = 500            # 正则最大字符数
_MAX_MATCHES = 100                # 最多返回的命中数
_REGEX_CONTEXT_CHARS = 200        # 命中两侧上下文扩展字符数
_MAX_MATCH_DISPLAY_CHARS = 240    # 单个命中展示的最大字符数
_MAX_WINDOW_CHARS = 640           # 单个窗口最大字符数
_SENTENCE_EXPANSION_WINDOW = 600
_SEARCH_TIMEOUT_SECONDS = 5       # 正则扫描超时
_TIMEOUT_SECONDS = 300.0          # 工具整体超时

_PARAMETERS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "content_ids": {
            "type": "array",
            "items": {"type": "string", "minLength": 1},
            "minItems": 1,
            "maxItems": 16,
            "description": "按给定顺序搜索的缓存工具输出内容 ID。",
        },
        "pattern": {
            "type": "string",
            "minLength": 1,
            "maxLength": _MAX_REGEX_CHARS,
            "description": "对每份完整源文本进行匹配的 Python 正则表达式。",
        },
    },
    "required": ["content_ids", "pattern"],
    "additionalProperties": False,
}


@dataclass(slots=True)
class ToolOutputRegexWindow:
    """一个正文上下文窗口及其中实际包含的正则命中次数。"""

    content_id: str
    text: str
    start_offset: int
    end_offset: int
    matched_count: int


@dataclass(slots=True)
class ToolOutputRegexSearchResult:
    """一次正则搜索的窗口及是否因命中上限截断。"""

    windows: list[ToolOutputRegexWindow]
    truncated: bool


class SearchToolOutputRegexTool:
    def __init__(self) -> None:
        self._definition = ToolDefinition(
            llm_spec=ToolLLMSpec(
                name="search_tool_output_regex",
                description=(
                    "Search cached tool output texts with a Python regular expression. "
                    "Search content_ids in the supplied order and return at most the first "
                    "100 matches. If truncated is true, narrow the pattern or reduce the "
                    "search scope. Each window includes up to 200 characters of nearby "
                    "context, expanded toward sentence boundaries. Overlapping windows from "
                    "the same content are merged; each window is at most 640 characters and "
                    "shows at most 240 characters of a single match. Window offsets are "
                    "absolute source offsets."
                ),
                parameters_schema=ToolParametersSchema(_PARAMETERS_SCHEMA),
            ),
            policy=ToolPolicy(
                expose_by_default=False,
                selection_mode=ToolSelectionMode.CONTEXTUAL,
                persist_output=True,
                risk_level=ToolRiskLevel.LOW,
                required_context_keys=("session_id",),
                timeout_seconds=_TIMEOUT_SECONDS,
            ),
            ui_spec=ToolUISpec(
                display_name="正则搜索缓存的工具输出",
                description="在多份缓存工具输出中按正则查找文本并返回上下文窗口。",
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
    ) -> ToolOutputRegexSearchResult:
        del config  # 未使用
        try:
            stored_items = []
            session_id = context["session_id"]
            # 按顺序去重加载缓存内容
            for content_id in dict.fromkeys(kwargs["content_ids"]):
                stored = await get_tool_content(
                    content_id=content_id,
                    session_id=session_id,
                )
                if stored is not None:
                    stored_items.append(stored)

            return await _search_by_regex(
                stored_items=stored_items,
                pattern=kwargs["pattern"],
            )
        except regex.error as exc:  # 正则非法
            raise ToolExecutionError(
                reason="invalid_regex_pattern",
                detail_reason=str(exc),
            ) from exc
        except TimeoutError as exc:  # 搜索超时
            raise ToolExecutionError(
                reason="regex_search_timeout",
                detail_reason=str(exc),
                retryable=False,
            ) from exc
        except Exception as exc:  # 其他异常
            raise ToolExecutionError(
                reason="search_cached_tool_output_by_regex_failed",
                detail_reason=str(exc),
                retryable=False,
            ) from exc


async def _search_by_regex(
    *,
    stored_items: Sequence[StoredToolContent],
    pattern: str,
) -> ToolOutputRegexSearchResult:
    """按正文顺序扫描；确认第 101 个命中后停止并标记结果已截断。"""

    def scan_loaded() -> ToolOutputRegexSearchResult:
        compiled = regex.compile(pattern)
        matches: list[tuple[StoredToolContent, int, int]] = []
        try:
            for stored in stored_items:
                for matched in compiled.finditer(
                    stored.text,
                    timeout=_SEARCH_TIMEOUT_SECONDS,
                ):
                    matches.append((stored, matched.start(), matched.end()))
                    if len(matches) > _MAX_MATCHES:  # 超出上限，截断
                        return ToolOutputRegexSearchResult(
                            windows=_build_regex_windows(matches[:_MAX_MATCHES]),
                            truncated=True,
                        )
        except TimeoutError as exc:
            raise TimeoutError(
                f"regex search exceeded {_SEARCH_TIMEOUT_SECONDS}s; results are incomplete"
            ) from exc

        return ToolOutputRegexSearchResult(
            windows=_build_regex_windows(matches),
            truncated=False,
        )

    # 放到线程中执行，避免阻塞事件循环
    return await asyncio.to_thread(scan_loaded)


def _build_regex_windows(
    matches: Sequence[tuple[StoredToolContent, int, int]],
) -> list[ToolOutputRegexWindow]:
    """先限制单个窗口长度，再合并同一正文中的相交窗口。"""

    windows: list[ToolOutputRegexWindow] = []
    active: ToolOutputRegexWindow | None = None  # 当前可合并的窗口

    for stored, match_start, match_end in matches:
        start, end = _regex_window_range(
            stored.text,
            match_start=match_start,
            match_end=match_end,
        )
        # 同一正文、与当前窗口相交且合并后不超长则合并
        if (
            active is not None
            and active.content_id == stored.content_id
            and start < active.end_offset
            and max(active.end_offset, end) - active.start_offset <= _MAX_WINDOW_CHARS
        ):
            old_start = active.start_offset
            active.end_offset = max(active.end_offset, end)
            active.text = stored.text[old_start:active.end_offset]
            active.matched_count += 1
            continue

        # 开启新窗口
        active = ToolOutputRegexWindow(
            content_id=stored.content_id,
            text=stored.text[start:end],
            start_offset=start,
            end_offset=end,
            matched_count=1,
        )
        windows.append(active)

    return windows


def _regex_window_range(
    text: str,
    *,
    match_start: int,
    match_end: int,
) -> tuple[int, int]:
    """每侧最多扩展 200 字符，并将单个窗口限制在 640 字符内。"""

    display_match_end = min(match_end, match_start + _MAX_MATCH_DISPLAY_CHARS)  # 命中展示上限
    left_boundary, right_boundary = _expand_match_to_sentences(
        text,
        start=match_start,
        end=display_match_end,
        before=1,
        after=1,
        window=_SENTENCE_EXPANSION_WINDOW,
    )

    if right_boundary - left_boundary <= _MAX_WINDOW_CHARS:  # 未超长直接返回
        return left_boundary, right_boundary

    # 超长则裁剪右边界，必要时同步左移左边界
    bounded_end = min(right_boundary, left_boundary + _MAX_WINDOW_CHARS)
    if bounded_end < display_match_end:  # 保证命中完整
        bounded_end = display_match_end
        left_boundary = max(0, bounded_end - _MAX_WINDOW_CHARS)
    return left_boundary, bounded_end


def _expand_match_to_sentences(
    text: str,
    *,
    start: int,
    end: int,
    before: int,
    after: int,
    window: int,
) -> tuple[int, int]:
    """使用 blingfire 的句子偏移，把命中范围扩展到邻近完整句子。"""

    source_start = max(0, start - window)
    source_end = min(len(text), end + window)
    _, offsets = text_to_sentences_and_offsets(text[source_start:source_end])
    absolute_offsets = [
        (sentence_start + source_start, sentence_end + source_start)
        for sentence_start, sentence_end in offsets
    ]
    if not absolute_offsets:
        return start, end

    first_complete = 0 if source_start == 0 else 1
    last_complete = (
        len(absolute_offsets) - 1
        if source_end == len(text)
        else len(absolute_offsets) - 2
    )
    starts = [sentence_start for sentence_start, _ in absolute_offsets]
    start_index = max(bisect_right(starts, start) - 1, 0)
    end_index = max(bisect_right(starts, max(end - 1, start)) - 1, 0)
    start_index = max(start_index - before, first_complete)
    end_index = min(end_index + after, max(last_complete, end_index))
    return absolute_offsets[start_index][0], absolute_offsets[end_index][1]
