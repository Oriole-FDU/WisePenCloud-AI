from __future__ import annotations

from typing import Any

import regex

from chat.application.tools.core import (
    ToolDefinition,
    ToolExecutionError,
    ToolLLMSpec,
    ToolParametersSchema,
    ToolPolicy,
    ToolRiskLevel,
)

from ..services.models import ToolContentRegexReadRequest, ToolContentRegexReadResult
from ..services.reader import ToolContentReader

_MAX_REGEX_CHARS = 500
_TIMEOUT_SECONDS = 300.0
_CONTENT_IDS_SCHEMA: dict[str, Any] = {
    "type": "array",
    "items": {"type": "string", "minLength": 1},
    "minItems": 1,
    "maxItems": 64,
    "description": "One or more cnt_* ids from previous content_receipts.",
}
_PARAMETERS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "content_ids": _CONTENT_IDS_SCHEMA,
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
            "description": "Maximum matches returned across all content_ids.",
        },
        "context_chars": {
            "type": "integer",
            "default": 1000,
            "minimum": 0,
            "description": "Source characters included before and after each exact match.",
        },
    },
    "required": ["content_ids", "pattern"],
    "additionalProperties": False,
}


class ToolContentRegexReadTool:
    """跨文档正则扫描权威原文。"""

    __slots__ = ("_definition", "_reader")

    def __init__(self, *, reader: ToolContentReader) -> None:
        self._reader = reader
        self._definition = ToolDefinition(
            llm_spec=ToolLLMSpec(
                name="tool_content_regex_read",
                description=(
                    "Search complete cached source texts with a Python regular expression. "
                    "Use this for exact names, identifiers, citations, headings, URLs, or other "
                    "literal patterns, including matches that cross retrieval chunk boundaries. "
                    "Results include absolute match offsets and bounded source context. Use "
                    "tool_content_ranked_read for semantic retrieval, tool_content_read_by_locator "
                    "for known page/section/anchor locators, and tool_content_read for known offsets."
                ),
                parameters_schema=ToolParametersSchema(_PARAMETERS_SCHEMA),
            ),
            policy=ToolPolicy(
                expose_by_default=True,
                persist_output=True,
                risk_level=ToolRiskLevel.LOW,
                required_context_keys=("session_id",),
                timeout_seconds=_TIMEOUT_SECONDS,
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
    ) -> ToolContentRegexReadResult:
        del config
        pattern = str(kwargs.get("pattern") or "")
        if not pattern:
            raise ToolExecutionError(reason="missing_pattern", detail_reason="pattern is required.")
        if len(pattern) > _MAX_REGEX_CHARS:
            raise ToolExecutionError(
                reason="regex_pattern_too_long",
                detail_reason=f"regex pattern is too long; max {_MAX_REGEX_CHARS} chars.",
            )
        try:
            regex.compile(pattern)
        except regex.error as exc:
            raise ToolExecutionError(reason="invalid_regex_pattern", detail_reason=str(exc)) from exc

        try:
            return await self._reader.read_regex(
                request=ToolContentRegexReadRequest(
                    content_ids=tuple(str(value) for value in kwargs["content_ids"]),
                    pattern=pattern,
                    max_matches=max(int(kwargs.get("max_matches", 10)), 0),
                    context_chars=max(int(kwargs.get("context_chars", 1000)), 0),
                ),
                session_id=str(context["session_id"]),
            )
        except Exception as exc:
            raise ToolExecutionError(
                reason="tool_content_regex_read_failed",
                detail_reason=str(exc),
                retryable=False,
            ) from exc
