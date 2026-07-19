from __future__ import annotations

from typing import Any

import regex
from chat.application.tools.common.tool_content_store import ToolContentStore
from chat.application.tools.core import (
    ToolDefinition,
    ToolExecutionError,
    ToolLLMSpec,
    ToolParametersSchema,
    ToolPolicy,
    ToolRiskLevel,
)
from chat.application.tools.utils.batching import batched

from ..services.content_loader import ToolContentLoader
from ..services.content_window_builder import ToolContentWindowBuilder
from ..services.models import (
    ToolContentReadFailure,
    ToolContentRegexReadRequest,
    ToolContentRegexReadResult,
    ToolContentSelector,
)
from ..services.readers import RegexMatchReader
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
            "description": "Required. Python regular expression used for exact pattern matching.",
        },
        "max_matches": {
            "type": "integer",
            "default": 10,
            "description": "Maximum number of matches returned across all content_ids.",
        },
        "merge_before": {"type": "integer", "default": 0},
        "merge_after": {"type": "integer", "default": 0},
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
            content_store: ToolContentStore,
            max_window_chars: int | None = None,
    ) -> None:
        self._reader = RegexMatchReader(
            loader=ToolContentLoader(store=content_store),
            window_builder=ToolContentWindowBuilder(max_chars=max_window_chars),
        )
        self._definition = ToolDefinition(
            llm_spec=ToolLLMSpec(
                name="tool_content_regex_read",
                description=(
                    "Find exact regular-expression matches from cached tool output across one or more content_ids.\n\n"
                    "WHEN TO TRIGGER:\n"
                    "  - MUST trigger when previous tool calls returned content_receipts and exact pattern matching is needed.\n"
                    "  - SHOULD trigger for IDs, URLs, headings, names, citations, or other precise text.\n"
                    "DO NOT TRIGGER when:\n"
                    "  - You need natural-language retrieval; use tool_content_ranked_expand_read.\n"
                    "  - You need sequential offset-based reading; use tool_content_sequential_read.\n\n"
                    "INPUT RULES:\n"
                    "  - Accepts up to 64 content_ids and reads them in bounded internal batches of 16.\n"
                    "  - selector prefilters chunks before matching; selector groups are intersected.\n"
                    "  - merge_before and merge_after expand windows around matched chunks.\n\n"
                    "OUTPUT RULES:\n"
                    "  - Returns matches and per-content failures without discarding successful batches.\n"
                    "  - This tool reads existing cnt_* content and never creates another content receipt."
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
        failed = []
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
                result = await self._reader.read(
                    request=request,
                    session_id=str(context["session_id"]),
                )
            except Exception as exc:
                failed.extend(
                    ToolContentReadFailure(content_id=content_id, reason=type(exc).__name__)
                    for content_id in batch
                )
                continue
            matches.extend(result.matches[:remaining_matches])
            failed.extend(result.failed)

        return ToolContentRegexReadResult(matches=tuple(matches), failed=tuple(failed))
