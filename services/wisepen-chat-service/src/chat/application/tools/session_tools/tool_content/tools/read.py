from __future__ import annotations

from typing import Any

from chat.application.tools.core import (
    ToolDefinition,
    ToolExecutionError,
    ToolLLMSpec,
    ToolParametersSchema,
    ToolPolicy,
    ToolRiskLevel,
)

from ..services.models import ToolContentGroupedReadResult, ToolContentRangeReadResult
from ..services.service import ToolContentService

_TIMEOUT_SECONDS = 300.0
_CONTENT_ID_SCHEMA: dict[str, Any] = {
    "type": "string",
    "minLength": 1,
    "description": "Required. One cnt_* id from a previous contents entry.",
}
_RANGE_PARAMETERS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "content_id": _CONTENT_ID_SCHEMA,
        "start": {
            "type": "integer",
            "description": (
                "Optional inclusive character offset. Negative values count from the end."
            ),
        },
        "end": {
            "type": "integer",
            "description": (
                "Optional exclusive character offset. Negative values count from the end."
            ),
        },
    },
    "required": ["content_id"],
    "additionalProperties": False,
}
_PAGES_PARAMETERS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "content_id": _CONTENT_ID_SCHEMA,
        "page_labels": {
            "type": "array",
            "items": {"type": "string", "minLength": 1},
            "minItems": 1,
            "maxItems": 20,
            "description": "Page labels returned by tool_content_get_snapshot.",
        },
    },
    "required": ["content_id", "page_labels"],
    "additionalProperties": False,
}
_SECTIONS_PARAMETERS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "content_id": _CONTENT_ID_SCHEMA,
        "section_paths": {
            "type": "array",
            "items": {"type": "string", "minLength": 1},
            "minItems": 1,
            "maxItems": 20,
            "description": (
                "Exact section_path strings returned by tool_content_get_snapshot, "
                "for example \"Methods > Dataset\"."
            ),
        },
    },
    "required": ["content_id", "section_paths"],
    "additionalProperties": False,
}


class ToolContentReadRangeTool:
    """按 offset 区间读取单文档权威原文。"""

    __slots__ = ("_definition", "_service")

    def __init__(self, *, service: ToolContentService) -> None:
        self._service = service
        self._definition = ToolDefinition(
            llm_spec=ToolLLMSpec(
                name="tool_content_read_range",
                description=(
                    "Read source text from one cached content_id by character range.\n\n"
                    "WHEN TO TRIGGER:\n"
                    "  - MUST trigger when you already know the exact offset range, the beginning, "
                    "or the end of cached content.\n"
                    "  - SHOULD trigger after snapshot/search results expose useful offsets.\n"
                    "DO NOT TRIGGER when:\n"
                    "  - You need pages or sections; use tool_content_read_pages or "
                    "tool_content_read_sections.\n"
                    "  - You need discovery; use tool_content_semantic_search or "
                    "tool_content_regex_search.\n\n"
                    "INPUT RULES:\n"
                    "  - Ranges use Python slice semantics: start is inclusive and end is exclusive.\n"
                    "  - Negative offsets count from the end; start=-1000 reads the final 1000 characters.\n"
                    "  - Omitting both offsets reads a token-budgeted window from the beginning.\n"
                    "  - If a requested range is truncated, continue from the returned end_offset."
                ),
                parameters_schema=ToolParametersSchema(_RANGE_PARAMETERS_SCHEMA),
            ),
            policy=_policy(),
        )

    @property
    def definition(self) -> ToolDefinition:
        return self._definition

    async def execute(
        self,
        context: dict[str, Any],
        config: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> ToolContentRangeReadResult:
        del config
        try:
            return await self._service.read_range(
                content_id=str(kwargs["content_id"]),
                session_id=str(context["session_id"]),
                start=int(kwargs["start"]) if "start" in kwargs else None,
                end=int(kwargs["end"]) if "end" in kwargs else None,
            )
        except Exception as exc:
            raise ToolExecutionError(
                reason="tool_content_read_range_failed",
                detail_reason=str(exc),
                retryable=False,
            ) from exc


class ToolContentReadPagesTool:
    """按页标签批量读取单文档权威原文。"""

    __slots__ = ("_definition", "_service")

    def __init__(self, *, service: ToolContentService) -> None:
        self._service = service
        self._definition = ToolDefinition(
            llm_spec=ToolLLMSpec(
                name="tool_content_read_pages",
                description=(
                    "Read one or more pages from one cached content_id by page labels.\n\n"
                    "WHEN TO TRIGGER:\n"
                    "  - MUST trigger when tool_content_get_snapshot or a previous result identifies "
                    "specific pages to inspect.\n"
                    "  - SHOULD keep page_labels tight and pass multiple needed pages in one call.\n"
                    "DO NOT TRIGGER when:\n"
                    "  - You need sections; use tool_content_read_sections.\n"
                    "  - You only know a semantic question or exact phrase; search first.\n\n"
                    "OUTPUT RULES:\n"
                    "  - items[] is grouped by requested page label.\n"
                    "  - budget_exhausted indicates that later page windows were omitted."
                ),
                parameters_schema=ToolParametersSchema(_PAGES_PARAMETERS_SCHEMA),
            ),
            policy=_policy(),
        )

    @property
    def definition(self) -> ToolDefinition:
        return self._definition

    async def execute(
        self,
        context: dict[str, Any],
        config: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> ToolContentGroupedReadResult:
        del config
        try:
            return await self._service.read_pages(
                content_id=str(kwargs["content_id"]),
                session_id=str(context["session_id"]),
                page_labels=tuple(str(value).strip() for value in kwargs["page_labels"]),
            )
        except Exception as exc:
            raise ToolExecutionError(
                reason="tool_content_read_pages_failed",
                detail_reason=str(exc),
                retryable=False,
            ) from exc


class ToolContentReadSectionsTool:
    """按 Section 路径批量读取单文档权威原文。"""

    __slots__ = ("_definition", "_service")

    def __init__(self, *, service: ToolContentService) -> None:
        self._service = service
        self._definition = ToolDefinition(
            llm_spec=ToolLLMSpec(
                name="tool_content_read_sections",
                description=(
                    "Read one or more sections from one cached content_id by section_path values.\n\n"
                    "WHEN TO TRIGGER:\n"
                    "  - MUST trigger when tool_content_get_snapshot identifies specific sections.\n"
                    "  - SHOULD pass multiple sibling or related section_paths in one call.\n"
                    "DO NOT TRIGGER when:\n"
                    "  - You need physical pages; use tool_content_read_pages.\n"
                    "  - You only know a semantic question or exact phrase; search first.\n\n"
                    "INPUT RULES:\n"
                    "  - section_paths are exact strings from snapshot, joined with \" > \".\n"
                    "OUTPUT RULES:\n"
                    "  - items[] is grouped by requested section_path.\n"
                    "  - budget_exhausted indicates that later section windows were omitted."
                ),
                parameters_schema=ToolParametersSchema(_SECTIONS_PARAMETERS_SCHEMA),
            ),
            policy=_policy(),
        )

    @property
    def definition(self) -> ToolDefinition:
        return self._definition

    async def execute(
        self,
        context: dict[str, Any],
        config: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> ToolContentGroupedReadResult:
        del config
        try:
            return await self._service.read_sections(
                content_id=str(kwargs["content_id"]),
                session_id=str(context["session_id"]),
                section_paths=tuple(
                    str(value).strip() for value in kwargs["section_paths"]
                ),
            )
        except Exception as exc:
            raise ToolExecutionError(
                reason="tool_content_read_sections_failed",
                detail_reason=str(exc),
                retryable=False,
            ) from exc


def _policy() -> ToolPolicy:
    return ToolPolicy(
        expose_by_default=True,
        persist_output=True,
        risk_level=ToolRiskLevel.LOW,
        required_context_keys=("session_id",),
        timeout_seconds=_TIMEOUT_SECONDS,
    )
