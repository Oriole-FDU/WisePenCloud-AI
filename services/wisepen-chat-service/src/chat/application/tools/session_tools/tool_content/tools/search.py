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

from ..services.models import (
    ToolContentRegexSearchRequest,
    ToolContentRegexSearchResult,
    ToolContentSemanticSearchRequest,
    ToolContentSemanticSearchResult,
)
from ..services.service import ToolContentService

_MAX_REGEX_CHARS = 500
_TIMEOUT_SECONDS = 300.0
_CONTENT_IDS_SCHEMA: dict[str, Any] = {
    "type": "array",
    "items": {"type": "string", "minLength": 1},
    "minItems": 1,
    "maxItems": 64,
    "description": "One or more cnt_* ids from previous contents entries.",
}
_SEMANTIC_PARAMETERS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "content_ids": _CONTENT_IDS_SCHEMA,
        "query": {
            "type": "string",
            "minLength": 1,
            "description": "Complete question the semantic source chunks should answer.",
        },
        "top_k": {
            "type": "integer",
            "default": 10,
            "minimum": 0,
            "description": "Maximum globally ranked semantic chunks returned.",
        },
    },
    "required": ["content_ids", "query"],
    "additionalProperties": False,
}
_REGEX_PARAMETERS_SCHEMA: dict[str, Any] = {
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


class ToolContentSemanticSearchTool:
    """跨缓存内容语义检索权威原文窗口。"""

    __slots__ = ("_definition", "_service")

    def __init__(self, *, service: ToolContentService) -> None:
        self._service = service
        self._definition = ToolDefinition(
            llm_spec=ToolLLMSpec(
                name="tool_content_semantic_search",
                description=(
                    "Search semantic chunks from one or more cached contents and return the most "
                    "relevant source windows. Chunks follow Markdown section semantics rather than "
                    "physical page boundaries. Each result includes known page, section, and anchor "
                    "metadata for deterministic follow-up reads.\n\n"
                    "Use tool_content_regex_search for exact patterns. Use read tools only after "
                    "you know the desired range, pages, or sections."
                ),
                parameters_schema=ToolParametersSchema(_SEMANTIC_PARAMETERS_SCHEMA),
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
    ) -> ToolContentSemanticSearchResult:
        del config
        query = str(kwargs.get("query") or "").strip()
        if not query:
            raise ToolExecutionError(reason="missing_query", detail_reason="query is required.")
        try:
            return await self._service.semantic_search(
                request=ToolContentSemanticSearchRequest(
                    content_ids=tuple(str(value) for value in kwargs["content_ids"]),
                    query=query,
                    top_k=max(int(kwargs.get("top_k", 10)), 0),
                ),
                session_id=str(context["session_id"]),
            )
        except Exception as exc:
            raise ToolExecutionError(
                reason="tool_content_semantic_search_failed",
                detail_reason=str(exc),
                retryable=False,
            ) from exc


class ToolContentRegexSearchTool:
    """跨缓存内容正则检索权威原文窗口。"""

    __slots__ = ("_definition", "_service")

    def __init__(self, *, service: ToolContentService) -> None:
        self._service = service
        self._definition = ToolDefinition(
            llm_spec=ToolLLMSpec(
                name="tool_content_regex_search",
                description=(
                    "Search complete cached source texts with a Python regular expression. "
                    "Use this for exact names, identifiers, citations, headings, URLs, or other "
                    "literal patterns, including matches that cross retrieval chunk boundaries. "
                    "Results include absolute match offsets and bounded source context. "
                    "Use tool_content_semantic_search for meaning-based retrieval. Use read tools "
                    "after you know the desired range, pages, or sections."
                ),
                parameters_schema=ToolParametersSchema(_REGEX_PARAMETERS_SCHEMA),
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
    ) -> ToolContentRegexSearchResult:
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
            return await self._service.regex_search(
                request=ToolContentRegexSearchRequest(
                    content_ids=tuple(str(value) for value in kwargs["content_ids"]),
                    pattern=pattern,
                    max_matches=max(int(kwargs.get("max_matches", 10)), 0),
                    context_chars=(
                        max(int(kwargs["context_chars"]), 0)
                        if "context_chars" in kwargs
                        else None
                    ),
                ),
                session_id=str(context["session_id"]),
            )
        except Exception as exc:
            raise ToolExecutionError(
                reason="tool_content_regex_search_failed",
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
