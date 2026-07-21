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
from chat.application.tools.utils.batching import batched

from ..services.models import (
    ToolContentRegexReadRequest,
    ToolContentRegexMatch,
    ToolContentSelector,
)
from ..services.reader import ToolContentReader
from .common import (
    CONTENT_IDS_SCHEMA,
    SELECTOR_SCHEMA,
)

_INTERNAL_BATCH_SIZE = 16
_MAX_REGEX_CHARS = 500
_TIMEOUT_SECONDS = 300.0
_PARAMETERS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "content_ids": CONTENT_IDS_SCHEMA,
        "selector": SELECTOR_SCHEMA,
        "pattern": {
            "type": "string",
            "minLength": 1,
            "maxLength": _MAX_REGEX_CHARS,
            "description": (
                "A Python regular expression scanned against each selected content's complete "
                "stored text. Markdown formulas and similar parsed content may contain malformed "
                "or unexpected markup, so use a tolerant pattern when extracting them."
            ),
        },
        "max_matches": {
            "type": "integer",
            "default": 10,
            "description": "Maximum number of occurrences returned across all content_ids.",
        },
        "merge_before": {
            "type": "integer",
            "default": 0,
            "description": "Number of adjacent chunks before each matching chunk to include as context.",
        },
        "merge_after": {
            "type": "integer",
            "default": 0,
            "description": "Number of adjacent chunks after each matching chunk to include as context.",
        },
    },
    "required": ["content_ids", "pattern"],
    "additionalProperties": False,
}


class ToolContentRegexReadTool:
    """跨文档正则读取已有 cnt_* 内容。"""

    __slots__ = ("_definition", "_reader")

    def __init__(
            self,
            *,
            reader: ToolContentReader,
    ) -> None:
        self._reader = reader
        self._definition = ToolDefinition(
            llm_spec=ToolLLMSpec(
                name="tool_content_regex_read",
                description=(
                    "Find exact text patterns in one or more cached cnt_* contents. Use this for "
                    "identifiers, names, URLs, headings, citations, numbers, or other patterns whose "
                    "wording is known but location is not. Use tool_content_read for known character "
                    "offsets, and tool_content_ranked_expand_read for semantic questions whose exact "
                    "wording is unknown. The regular expression scans each selected content's complete "
                    "stored text, so a match may cross chunk boundaries; each occurrence is then mapped "
                    "to its matching chunk and returned with optional neighboring chunks. Matching does "
                    "not repair or normalize Markdown. Parsed formulas and similar content may be "
                    "malformed, so use broader tolerant patterns when needed."
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
    ) -> tuple[ToolContentRegexMatch, ...]:
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

        content_ids = tuple(str(value) for value in kwargs["content_ids"])
        max_matches = max(int(kwargs.get("max_matches", 10)), 0)
        matches = []
        for batch in batched(content_ids, batch_size=_INTERNAL_BATCH_SIZE):
            remaining_matches = max_matches - len(matches)
            if remaining_matches <= 0:
                break

            request = ToolContentRegexReadRequest(
                content_ids=batch,
                pattern=pattern,
                selector=ToolContentSelector.from_payload(kwargs.get("selector")),
                max_matches=remaining_matches,
                merge_before=int(kwargs.get("merge_before", 0)),
                merge_after=int(kwargs.get("merge_after", 0)),
            )
            try:
                result = await self._reader.read_regex(
                    request=request,
                    session_id=str(context["session_id"]),
                )
            except Exception:
                continue
            matches.extend(result[:remaining_matches])

        return tuple(matches)
