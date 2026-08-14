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
    ToolContentStore as CachedToolOutputStore,
)
from chat.core.config.app_settings import settings

from chat.application.tools.session_tools.cached_tool_output_tools.window import CachedToolOutputWindow, CachedToolOutputWindowBuilder

_MAX_REGEX_CHARS = 500
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
            "default": 10,
            "minimum": 0,
            "description": "Maximum matches returned across all cached tool outputs.",
        },
        "context_chars": {
            "type": "integer",
            "minimum": 0,
            "description": (
                "Optional raw source characters included before and after each match. "
                "When omitted, the reader chooses token-budgeted context automatically."
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


@dataclass(slots=True)
class CachedToolOutputSearchByRegexResult:
    matches: list[CachedToolOutputSearchByRegexMatch] = field(default_factory=list)
    budget_exhausted: bool = False


class CachedToolOutputSearchByRegexTool:
    __slots__ = ("_definition", "_store")

    def __init__(self, *, store: CachedToolOutputStore) -> None:
        self._store = store
        self._definition = ToolDefinition(
            llm_spec=ToolLLMSpec(
                name="search_cached_tool_output_by_regex",
                description=(
                    "Search complete cached source texts with a Python regular expression. "
                    "Use this for exact names, identifiers, citations, headings, URLs, or other "
                    "literal patterns, including matches that cross retrieval chunk boundaries. "
                    "Results include absolute match offsets and bounded source context. "
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
    ) -> CachedToolOutputSearchByRegexResult:
        del config
        # pattern 为空直接视为调用错误；不要把空正则扩展成全文匹配。
        pattern = str(kwargs.get("pattern") or "")
        if not pattern:
            raise ToolExecutionError(
                reason="missing_pattern",
                detail_reason="pattern is required.",
            )
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
            session_id = str(context["session_id"])
            for content_id in [str(value) for value in kwargs["content_ids"]]:
                stored = await self._store.get(
                    content_id=content_id,
                    session_id=session_id,
                )
                if stored is not None:
                    stored_items.append(stored)
            return await _search_by_regex(
                stored_items=stored_items,
                pattern=pattern,
                max_matches=max(int(kwargs.get("max_matches", 10)), 0),
                context_chars=(
                    max(int(kwargs["context_chars"]), 0)
                    if "context_chars" in kwargs
                    else None
                ),
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
    context_chars: int | None,
) -> CachedToolOutputSearchByRegexResult:
    if max_matches <= 0:
        # max_matches=0 是合法输入，用于主动禁止返回命中。
        return CachedToolOutputSearchByRegexResult()

    # regex 的窗口预算使用独立配置，不复用 read_cached_tool_output_by_range 的单窗口预算。
    builder = CachedToolOutputWindowBuilder(
        char_budget=settings.TOOL_CONTENT_REGEX_TOTAL_CHAR_BUDGET
    )

    def scan_loaded() -> tuple[list[CachedToolOutputSearchByRegexMatch], bool]:
        # regex 搜索放到工作线程里执行，避免复杂表达式阻塞事件循环。
        compiled = regex.compile(pattern)
        matches: list[CachedToolOutputSearchByRegexMatch] = []
        remaining = settings.TOOL_CONTENT_REGEX_TOTAL_CHAR_BUDGET
        for stored in stored_items:
            try:
                # timeout 由 regex 库在单次扫描中控制，复杂表达式会转成工具错误返回。
                for matched in compiled.finditer(
                    stored.text,
                    timeout=_SEARCH_TIMEOUT_SECONDS,
                ):
                    if remaining <= 0:
                        return matches, True
                    window_start, window_end = _regex_window_range(
                        stored.text,
                        match_start=matched.start(),
                        match_end=matched.end(),
                        context_chars=context_chars,
                        context_side_char_budget=(
                            settings.TOOL_CONTENT_REGEX_CONTEXT_SIDE_CHAR_BUDGET
                        ),
                        total_char_budget=remaining,
                    )
                    # 命中上下文最终仍通过 WindowBuilder 回填 page/section/anchor 元数据。
                    window = builder.build_range_window(
                        stored,
                        start=window_start,
                        end=window_end,
                        char_budget=remaining,
                    )
                    matches.append(
                        CachedToolOutputSearchByRegexMatch(
                            content_id=stored.content_id,
                            match_start=matched.start(),
                            match_end=matched.end(),
                            window=window,
                        )
                    )
                    remaining -= len(window.text)
                    if len(matches) >= max_matches:
                        # 达到命中数量上限不是预算耗尽，不需要标记 budget_exhausted。
                        return matches, False
            except TimeoutError as exc:
                raise TimeoutError(
                    f"regex search exceeded {_SEARCH_TIMEOUT_SECONDS}s"
                ) from exc
        return matches, False

    matches, budget_exhausted = await asyncio.to_thread(scan_loaded)
    return CachedToolOutputSearchByRegexResult(
        matches=matches,
        budget_exhausted=budget_exhausted,
    )


def _regex_window_range(
    text: str,
    *,
    match_start: int,
    match_end: int,
    context_chars: int | None,
    context_side_char_budget: int,
    total_char_budget: int,
) -> tuple[int, int]:
    # 未指定 context_chars 时使用系统默认的左右上下文预算。
    if context_chars is None:
        candidate_start = max(match_start - context_side_char_budget, 0)
        candidate_end = min(match_end + context_side_char_budget, len(text))
    else:
        # 调用方显式给出 context_chars 时，按该值覆盖默认上下文长度。
        context_chars = max(context_chars, 0)
        candidate_start = max(match_start - context_chars, 0)
        candidate_end = min(match_end + context_chars, len(text))

    if len(text[candidate_start:candidate_end]) <= total_char_budget:
        # 候选窗口未超过剩余预算时直接返回完整上下文。
        return candidate_start, candidate_end

    match_chars = len(text[match_start:match_end])
    if match_chars >= total_char_budget:
        # 极端情况下命中文本本身已超过预算，至少返回完整命中范围。
        return match_start, match_end

    # 上下文超过预算时，优先保留命中本体，再把剩余预算尽量平均分给前后文。
    context_budget = total_char_budget - match_chars
    before_budget = context_budget // 2
    start = max(match_start - before_budget, candidate_start)
    after_budget = context_budget - len(text[start:match_start])
    return start, min(match_end + after_budget, candidate_end)


def _policy() -> ToolPolicy:
    return ToolPolicy(
        expose_by_default=False,
        selection_mode=ToolSelectionMode.CONTEXTUAL,
        persist_output=True,
        risk_level=ToolRiskLevel.LOW,
        required_context_keys=("session_id",),
        timeout_seconds=_TIMEOUT_SECONDS,
    )
