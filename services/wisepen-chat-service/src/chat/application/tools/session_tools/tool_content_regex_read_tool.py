from __future__ import annotations

import re
from typing import Any

from chat.application.tools.common.tool_content_store import ToolContentStore
from chat.application.tools.core import (
    ToolDefinition,
    ToolExecutionError,
    ToolLLMSpec,
    ToolParametersSchema,
    ToolPolicy,
    ToolRiskLevel,
)
from chat.application.tools.session_tools.tool_content_read.tool_common import (
    CONTENT_IDS_SCHEMA,
    SELECTOR_SCHEMA,
    read_content_id_batches,
    selector_from_payload,
)
from chat.application.tools.session_tools.tool_content_read.models import (
    ToolContentRegexReadRequest,
    ToolContentReadResult,
)
from chat.application.tools.session_tools.tool_content_read.content_loader import (
    ToolContentLoader,
)
from chat.application.tools.session_tools.tool_content_read.content_window_builder import (
    ToolContentWindowBuilder,
)
from chat.application.tools.session_tools.tool_content_read.readers import (
    RegexMatchReader,
)


MAX_CONTENT_IDS = 16
MAX_REGEX_PATTERN_CHARS = 500
TOOL_CONTENT_READ_TIMEOUT_SECONDS = 300.0
DEFAULT_MAX_MATCHES = 10

PARAMETERS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "content_ids": CONTENT_IDS_SCHEMA,
        "selector": SELECTOR_SCHEMA,
        "pattern": {
            "type": "string",
            "minLength": 1,
            "maxLength": MAX_REGEX_PATTERN_CHARS,
            "description": "Required. Python regular expression used for exact pattern matching.",
        },
        "max_matches": {
            "type": "integer",
            "default": DEFAULT_MAX_MATCHES,
            "description": "Maximum number of matches across all content_ids.",
        },
        "merge_before": {
            "type": "integer",
            "default": 0,
            "description": "Number of chunks to include before each matched center chunk.",
        },
        "merge_after": {
            "type": "integer",
            "default": 0,
            "description": "Number of chunks to include after each matched center chunk.",
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
            content_store: ToolContentStore,
            max_window_chars: int | None = None,
    ) -> None:
        self._reader = RegexMatchReader(
            loader=ToolContentLoader(store=content_store),
            window_builder=ToolContentWindowBuilder(max_window_chars=max_window_chars),
        )
        self._definition = ToolDefinition(
            llm_spec=ToolLLMSpec(
                name="tool_content_regex_read",
                description=(
                    "Find exact regular-expression matches from cached tool output across one or more content_ids.\n"
                    "\n"
                    "WHEN TO TRIGGER:\n"
                    "  - MUST trigger when a previous tool returned a <content_receipt> and you need exact pattern matching.\n"
                    "  - SHOULD trigger for IDs, URLs, headings, names, citations, or other precise text patterns.\n"
                    "DO NOT TRIGGER when:\n"
                    "  - You need natural-language retrieval — use tool_content_rerank_read instead.\n"
                    "  - You need sequential offset-based reading from one content_id — use tool_content_sequential_read instead.\n"
                    "  - You need new content from the web — use web_fetch or web_crawl instead.\n"
                    "\n"
                    "INPUT RULES:\n"
                    "  - content_ids MUST be cnt_* ids from previous content receipts; multiple ids are read in bounded internal batches.\n"
                    "  - pattern is required and must be a Python regular expression.\n"
                    "  - selector optionally prefilters chunks by block_kinds, sections, page_labels, anchor_labels, or chunk_indices.\n"
                    "  - merge_before/merge_after expand windows around center chunks.\n"
                    "\n"
                    "OUTPUT RULES:\n"
                    "  - Returns matches across content_ids, each carrying its content_id and readable window.\n"
                    "  - This tool reads existing cnt_* content and never creates another content receipt.\n"
                ),
                parameters_schema=ToolParametersSchema(PARAMETERS_SCHEMA),
            ),
            policy=ToolPolicy(
                expose_by_default=True,
                persist_output=True,
                risk_level=ToolRiskLevel.LOW,
                required_context_keys=("session_id",),
                timeout_seconds=TOOL_CONTENT_READ_TIMEOUT_SECONDS,
            ),
        )

    @property
    def definition(self) -> ToolDefinition:
        return self._definition

    async def execute(self, context: dict[str, Any], **kwargs: Any) -> ToolContentReadResult:
        pattern = str(kwargs.get("pattern") or "")
        if not pattern:
            raise ToolExecutionError(
                reason="missing_pattern",
                detail_reason="pattern is required.",
                retryable=False,
            )
        if len(pattern) > MAX_REGEX_PATTERN_CHARS:
            raise ToolExecutionError(
                reason="regex_pattern_too_long",
                detail_reason=f"regex pattern is too long; max {MAX_REGEX_PATTERN_CHARS} chars.",
                retryable=False,
            )
        try:
            re.compile(pattern)
        except re.error as exc:
            raise ToolExecutionError(
                reason="invalid_regex_pattern",
                detail_reason=str(exc),
                retryable=False,
            ) from exc

        request = ToolContentRegexReadRequest(
            content_ids=tuple(str(value) for value in kwargs["content_ids"]),
            pattern=pattern,
            selector=selector_from_payload(kwargs.get("selector")),
            max_matches=int(kwargs.get("max_matches") or DEFAULT_MAX_MATCHES),
            merge_before=int(kwargs.get("merge_before") or 0),
            merge_after=int(kwargs.get("merge_after") or 0),
        )

        try:
            return await read_content_id_batches(
                request=request,
                batch_size=MAX_CONTENT_IDS,
                read_batch=lambda batch_request: self._reader.read(
                    request=batch_request,
                    session_id=str(context["session_id"]),
                ),
            )
        except ToolExecutionError:
            raise
        except Exception as exc:
            raise ToolExecutionError(
                reason="tool_content_regex_read_failed",
                detail_reason=str(exc),
                retryable=False,
            ) from exc
