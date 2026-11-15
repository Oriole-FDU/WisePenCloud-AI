from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

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
from chat.application.tools.core.output_cache.cache_store import ToolContentStore
from chat.core.config.app_settings import settings

_TIMEOUT_SECONDS = 300.0
_PARAMETERS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "content_id": {
            "type": "string",
            "minLength": 1,
            "description": "Required. One cached tool output content_id returned in a previous tool result.",
        },
        "start_item": {
            "type": "integer",
            "minimum": 0,
            "default": 0,
            "description": "0-based item index to start from.",
        },
        "start_char": {
            "type": "integer",
            "minimum": 0,
            "default": 0,
            "description": (
                "Character offset within start_item to continue from. Only used for text "
                "items (a truncated long line); for JSON items it is always 0."
            ),
        },
    },
    "required": ["content_id"],
    "additionalProperties": False,
}


@dataclass(slots=True)
class ToolOutputItemsResult:
    """一次条目窗口及续读游标

    - items：本次返回的条目（文本行的字符串，或 JSON 条目对象）
    - next_item / next_char：下次续读游标，原样回传即可
    - has_more：后面还有内容
    - truncated：文本模式下最后一条被截断（未完），下次应在同一 item 的 next_char 处继续
    """

    content_id: str
    items: list[Any] = field(default_factory=list)
    next_item: int = 0
    next_char: int = 0
    has_more: bool = False
    total_items: int = 0
    truncated: bool = False
    reason: str | None = None


class ReadToolOutputItemsTool:
    """按条目读取缓存的代码/日志/长列表输出

    用游标 (start_item, start_char) 定位
    JSON 条目原子返回、按下标推进
    文本条目（行）正常整行推进，遇到超长行时在行内按字符偏移续读。
    AI 只需原样回传 next_item / next_char 即可连续翻页
    """

    def __init__(self, *, tool_content_store: ToolContentStore) -> None:
        self.tool_content_store = tool_content_store
        self._definition = ToolDefinition(
            llm_spec=ToolLLMSpec(
                name="read_tool_output_items",
                description=(
                    "Read one cached item sequence (code, logs, long lists) by item range.\n\n"
                    "Items are returned from start_item until the configured character budget "
                    "is reached. Text items are whole lines; JSON items are whole objects.\n\n"
                    "CONTINUATION RULE:\n"
                    "  - If has_more is true, continue with start_item=next_item and "
                    "start_char=next_char (echo both values exactly).\n"
                    "  - If truncated is true, the last text item was cut mid-line; the next "
                    "page continues the SAME item at next_char."
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
                display_name="按条目读取缓存的工具输出",
                description="按条目读取缓存的代码、日志或长列表工具输出。",
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
    ) -> ToolOutputItemsResult:
        del config
        try:
            content_id = kwargs["content_id"]
            stored = await self.tool_content_store.get_tool_content(
                content_id=content_id,
                session_id=context["session_id"],
            )
            if stored is None:
                return ToolOutputItemsResult(content_id=content_id, reason="cached_tool_output_not_found")

            try:
                value = json.loads(stored.text)
            except json.JSONDecodeError:
                value = None
            is_json = isinstance(value, list)
            items: list[Any] = list(value) if is_json else stored.text.splitlines()

            start_item = int(kwargs.get("start_item") or 0)
            char_budget = settings.tools.content.read_window_char_budget
            if is_json:
                window_items, next_item, next_char, has_more, truncated = _read_json_items(
                    items, start_item, char_budget
                )
            else:
                start_char = int(kwargs.get("start_char") or 0)
                window_items, next_item, next_char, has_more, truncated = _read_text_items(
                    items, start_item, start_char, char_budget
                )
            return ToolOutputItemsResult(
                content_id=content_id,
                items=window_items,
                next_item=next_item,
                next_char=next_char,
                has_more=has_more,
                total_items=len(items),
                truncated=truncated,
            )
        except Exception as exc:
            raise ToolExecutionError(
                reason="read_cached_tool_output_items_failed",
                detail_reason=str(exc),
                retryable=False,
            ) from exc


def _read_json_items(
    items: list[Any],
    start_item: int,
    char_budget: int,
) -> tuple[list[Any], int, int, bool, bool]:
    """JSON 条目原子返回；按条目下标推进，无行内字符续读"""
    char_budget = max(1, char_budget)
    total = len(items)
    start_item = max(0, start_item)
    if start_item >= total:
        return [], start_item, 0, False, False

    result: list[Any] = []
    used = 0
    idx = start_item
    while idx < total:
        cost = len(json.dumps(items[idx], ensure_ascii=False)) + 1
        if result and used + cost > char_budget:
            break
        result.append(items[idx])
        used += cost
        idx += 1
        if used >= char_budget:
            break
    return result, idx, 0, idx < total, False


def _read_text_items(
    items: list[str],
    start_item: int,
    start_char: int,
    char_budget: int,
) -> tuple[list[str], int, int, bool, bool]:
    """文本条目（行）：复合游标，超长行在行内按字符偏移续读"""
    char_budget = max(1, char_budget)
    total = len(items)
    start_item = max(0, start_item)
    start_char = max(0, start_char)

    if not items or start_item >= total:
        return [], start_item, start_char, False, False

    # start_char 越过当前行末尾：跳到下一行开头
    while start_item < total and start_char >= len(items[start_item]):
        start_item += 1
        start_char = 0
    if start_item >= total:
        return [], start_item, 0, False, False

    result: list[str] = []
    idx = start_item
    char_idx = start_char

    # 第一段是当前行的剩余部分；它自己就超预算时截断，并在同一行内续读
    first = items[idx][char_idx:]
    if len(first) > char_budget:
        return [first[:char_budget]], idx, char_idx + char_budget, True, True

    result.append(first)
    used = len(first)
    idx += 1
    char_idx = 0

    # 后续整行，再加会超预算就停在整行边界
    while idx < total:
        line = items[idx]
        if used + len(line) > char_budget:
            break
        result.append(line)
        used += len(line)
        idx += 1
        if used >= char_budget:
            break

    return result, idx, 0, idx < total, False
