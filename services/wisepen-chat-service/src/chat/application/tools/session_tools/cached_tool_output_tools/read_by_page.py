from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from common.utils.document import Page

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
    ToolContentStore as CachedToolOutputStore,
)
from chat.application.tools.session_tools.cached_tool_output_tools.window import (
    CachedToolOutputWindow,
    CachedToolOutputWindowBuilder,
)
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
        "page_labels": {
            "type": "array",
            "items": {"type": "string", "minLength": 1},
            "minItems": 1,
            "maxItems": 20,
            "description": "Page labels returned by inspect_cached_tool_output_structure.",
        },
    },
    "required": ["content_id", "page_labels"],
    "additionalProperties": False,
}


@dataclass(slots=True)
class CachedToolOutputReadByPageItem:
    page_label: str
    windows: list[CachedToolOutputWindow] = field(default_factory=list)
    reason: str | None = None


@dataclass(slots=True)
class CachedToolOutputReadByPageResult:
    content_id: str
    items: list[CachedToolOutputReadByPageItem] = field(default_factory=list)
    budget_exhausted: bool = False


class CachedToolOutputReadByPageTool:
    __slots__ = ("_definition", "_store")

    def __init__(self, *, store: CachedToolOutputStore) -> None:
        self._store = store
        self._definition = ToolDefinition(
            llm_spec=ToolLLMSpec(
                name="read_cached_tool_output_by_page",
                description=(
                    "Read one or more pages from one cached tool output content_id by page labels.\n\n"
                    "WHEN TO TRIGGER:\n"
                    "  - MUST trigger when inspect_cached_tool_output_structure or a previous result identifies "
                    "specific pages to inspect.\n"
                    "  - SHOULD keep page_labels tight and pass multiple needed pages in one call.\n"
                    "DO NOT TRIGGER when:\n"
                    "  - You need sections; use read_cached_tool_output_by_section.\n"
                    "  - You only know a semantic question or exact phrase; search first.\n\n"
                    "OUTPUT RULES:\n"
                    "  - items[].page_label echoes the requested page label.\n"
                    "  - budget_exhausted indicates that later page windows were omitted."
                ),
                parameters_schema=ToolParametersSchema(_PARAMETERS_SCHEMA),
            ),
            policy=_policy(),
            ui_spec=ToolUISpec(
                display_name="按页读取缓存的工具输出",
                description="根据缓存工具输出结构中的页标签读取指定页面正文。",
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
    ) -> CachedToolOutputReadByPageResult:
        del config
        try:
            content_id = str(kwargs["content_id"])
            # 入参来自 JSON array，保持 list 语义；后续去重时仍保留用户给出的顺序。
            page_labels = [str(value).strip() for value in kwargs["page_labels"]]
            # page 读取同样走 session_id 隔离，不能只依赖 content_id 本身。
            stored = await self._store.get(
                content_id=content_id,
                session_id=str(context["session_id"]),
            )
            if stored is None:
                # content 不存在时仍逐个回显 page_label，调用方能知道哪些请求未被满足。
                unique_labels = list(dict.fromkeys(page_labels))
                return CachedToolOutputReadByPageResult(
                    content_id=content_id,
                    items=[
                        CachedToolOutputReadByPageItem(
                            page_label=page_label,
                            reason="cached_tool_output_not_found",
                        )
                        for page_label in unique_labels
                    ],
                )
            return _read_by_page(
                content_id=content_id,
                page_labels=page_labels,
                pages=stored.pages,
                builder=CachedToolOutputWindowBuilder(
                    char_budget=settings.TOOL_CONTENT_READ_WINDOW_CHAR_BUDGET
                ),
                stored=stored,
            )
        except Exception as exc:
            raise ToolExecutionError(
                reason="read_cached_tool_output_by_page_failed",
                detail_reason=str(exc),
                retryable=False,
            ) from exc


def _read_by_page(
    *,
    content_id: str,
    page_labels: Sequence[str],
    pages: Sequence[Page],
    builder: CachedToolOutputWindowBuilder,
    stored: StoredCachedToolOutput,
) -> CachedToolOutputReadByPageResult:
    # 同一页可能被模型重复请求，去重但不打乱首次出现顺序。
    unique_page_labels = list(dict.fromkeys(page_labels))
    # Page 是结构阶段产出的确定页范围，读取正文时只按其 source_span 回原文。
    pages_by_label = _pages_by_label(pages)
    items: list[CachedToolOutputReadByPageItem] = []
    remaining = settings.TOOL_CONTENT_READ_TOTAL_CHAR_BUDGET
    budget_exhausted = False

    for page_label in unique_page_labels:
        if remaining <= 0:
            # 总预算耗尽后继续返回占位 item，避免调用方误以为后续页不存在。
            budget_exhausted = True
            items.append(
                CachedToolOutputReadByPageItem(
                    page_label=page_label,
                    reason="page_budget_exhausted",
                )
            )
            continue

        page_ranges = pages_by_label.get(page_label, [])
        if not page_ranges:
            # page label 来自外部输入，索引中找不到时明确标记 page_not_found。
            items.append(
                CachedToolOutputReadByPageItem(
                    page_label=page_label,
                    reason="page_not_found",
                )
            )
            continue

        windows = []
        reason = None
        for page in page_ranges:
            if remaining <= 0:
                # 一个 page label 可能对应多个连续片段，片段之间共享同一轮总预算。
                budget_exhausted = True
                reason = "page_budget_exhausted"
                break
            window = builder.build_range_window(
                stored,
                start=page.source_span.start_offset,
                end=page.source_span.end_offset,
                char_budget=remaining,
            )
            windows.append(window)
            remaining -= len(window.text)
            if window.truncated:
                # 单个窗口被截断时，不再继续同页后续片段，提示调用方按 offset 续读。
                budget_exhausted = True
                reason = "page_budget_exhausted"
                break
        items.append(
            CachedToolOutputReadByPageItem(
                page_label=page_label,
                windows=windows,
                reason=reason,
            )
        )

    return CachedToolOutputReadByPageResult(
        content_id=content_id,
        items=items,
        budget_exhausted=budget_exhausted,
    )


def _pages_by_label(pages: Sequence[Page]) -> dict[str, list[Page]]:
    # 重复页标签保留为多个范围，按 parser 产生的原文顺序依次读取。
    indexed: dict[str, list[Page]] = {}
    for page in pages:
        indexed.setdefault(page.page_label, []).append(page)
    return indexed


def _policy() -> ToolPolicy:
    return ToolPolicy(
        expose_by_default=False,
        selection_mode=ToolSelectionMode.CONTEXTUAL,
        persist_output=True,
        risk_level=ToolRiskLevel.LOW,
        required_context_keys=("session_id",),
        timeout_seconds=_TIMEOUT_SECONDS,
    )
