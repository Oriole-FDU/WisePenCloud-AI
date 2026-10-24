from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

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
from chat.application.tools.session_tools.tool_output.read_by_range import (
    ToolOutputWindow,
)
from chat.core.config.app_settings import settings

_TIMEOUT_SECONDS = 300.0
_PARAMETERS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "content_id": {"type": "string", "minLength": 1},
        "section_id": {
            "type": "string",
            "minLength": 1,
            "description": "Exact section ID from the cached structure outline.",
        },
        "scope": {
            "type": "string",
            "enum": ["own", "subtree"],
            "default": "own",
            "description": "Read this section's own span or its complete subtree span.",
        },
        "start_offset": {
            "type": "integer",
            "minimum": 0,
            "default": 0,
            "description": "Absolute source character offset from which to continue reading.",
        },
    },
    "required": ["content_id", "section_id"],
    "additionalProperties": False,
}


@dataclass(slots=True)
class ToolOutputSectionResult:
    """章节窗口及原文续读位置。"""

    content_id: str
    section_id: str
    title: str
    section_path: str
    window: ToolOutputWindow
    next_offset: int
    has_more: bool
    reason: str | None = None

    @classmethod
    def missing(
        cls,
        content_id: str,
        section_id: str,
        start_offset: int,
    ) -> ToolOutputSectionResult:
        """章节不存在时的空结果；续读位置保持请求原值。"""
        return cls(
            content_id=content_id,
            section_id=section_id,
            title="",
            section_path="",
            window=ToolOutputWindow(text="", start_offset=0, end_offset=0),
            next_offset=start_offset,
            has_more=False,
            reason="section_not_found",
        )


class ReadToolOutputSectionTool:
    def __init__(self) -> None:
        self._definition = ToolDefinition(
            llm_spec=ToolLLMSpec(
                name="read_tool_output_section",
                description=(
                    "Read one cached section from its original source span. "
                    "scope=own reads through the next heading at the same or "
                    "higher tree level; scope=subtree includes all descendants. "
                    "Use the absolute character start_offset to continue a long section from "
                    "the returned next_offset while has_more is true."
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
                display_name="按章节读取缓存的工具输出",
                description="按原始章节范围和字符偏移读取一个章节。",
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
    ) -> ToolOutputSectionResult:
        del config
        try:
            content_id = kwargs["content_id"]
            section_id = kwargs["section_id"]
            scope = kwargs["scope"]
            start_offset = kwargs["start_offset"]

            stored = await get_tool_content(
                content_id=content_id,
                session_id=context["session_id"],
            )
            if stored is None:
                return ToolOutputSectionResult.missing(content_id, section_id, start_offset)
            return _read_by_section(
                stored=stored,
                section_id=section_id,
                scope=scope,
                start_offset=start_offset,
                char_budget=settings.TOOL_CONTENT_READ_WINDOW_CHAR_BUDGET,
            )
        except Exception as exc:
            raise ToolExecutionError(
                reason="read_cached_tool_output_by_section_failed",
                detail_reason=str(exc),
                retryable=False,
            ) from exc


def _read_by_section(
    *,
    section_id: str,
    scope: Literal["own", "subtree"],
    start_offset: int,
    stored: StoredToolContent,
    char_budget: int,
) -> ToolOutputSectionResult:
    """在章节范围内按原文绝对坐标续读；默认零起点收敛到章节起点。"""
    section = stored.sections_by_id.get(section_id)
    if section is None:
        return ToolOutputSectionResult.missing(stored.content_id, section_id, start_offset)

    span = section.own_span if scope == "own" else section.subtree_span
    window_start = min(max(start_offset, span.start_offset), span.end_offset)
    window_end = min(window_start + char_budget, span.end_offset)
    return ToolOutputSectionResult(
        content_id=stored.content_id,
        section_id=section.section_id,
        title=section.title,
        section_path=" > ".join(section.section_path),
        window=ToolOutputWindow(
            text=stored.text[window_start:window_end],
            start_offset=window_start,
            end_offset=window_end,
        ),
        next_offset=window_end,
        has_more=window_end < span.end_offset,
    )
