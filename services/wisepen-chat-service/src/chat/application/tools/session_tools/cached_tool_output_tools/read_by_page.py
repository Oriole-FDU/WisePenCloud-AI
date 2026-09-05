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
from chat.application.tools.core.output_cache.cache_store import get_tool_content
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
class PageContent:
    """单个 page 的模型可见内容；结构标识与续读窗口分开表达。"""

    page_label: str
    window: CachedToolOutputWindow


@dataclass(slots=True)
class CachedToolOutputReadByPageResult:
    """按 page 读取结果；只返回实际成功构造的 page 内容。"""

    content_id: str
    page_contents: list[PageContent] = field(default_factory=list)


class CachedToolOutputReadByPageTool:

    def __init__(self) -> None:
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
                    "  - page_contents contains one entry per page successfully read.\n"
                    "  - Each entry has page_label and one window; continue a truncated window from its end_offset."
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
            content_id = kwargs["content_id"]
            page_labels = kwargs["page_labels"]
            stored = await get_tool_content(
                content_id=content_id,
                session_id=context["session_id"],
            )
            if stored is None:
                return CachedToolOutputReadByPageResult(content_id=content_id)
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
    pages_by_label = _pages_by_label(pages)
    page_contents: list[PageContent] = []
    remaining = settings.TOOL_CONTENT_READ_TOTAL_CHAR_BUDGET

    # page label 去重但保留首次出现顺序；一个 label 的多个范围只构造一个窗口。
    for page_label in dict.fromkeys(page_labels):
        if remaining <= 0:
            break
        page_ranges = pages_by_label.get(page_label)
        if not page_ranges:
            continue

        window = builder.build_spans_window(
            stored,
            source_spans=[page.source_span for page in page_ranges],
            char_budget=remaining,
        )
        page_contents.append(
            PageContent(
                page_label=page_label,
                window=window,
            )
        )
        remaining -= len(window.text)
        if window.truncated:
            break

    return CachedToolOutputReadByPageResult(
        content_id=content_id,
        page_contents=page_contents,
    )


def _pages_by_label(pages: Sequence[Page]) -> dict[str, list[Page]]:
    # 重复页标签保留为多个范围，按 parser 产生的原文顺序聚合读取。
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
