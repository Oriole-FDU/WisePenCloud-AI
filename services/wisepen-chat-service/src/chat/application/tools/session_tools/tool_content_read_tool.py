from __future__ import annotations

from typing import Any

from chat.application.tools.common.batching import batched
from chat.application.tools.common.tool_content_store import ToolContentStore
from chat.application.tools.core import (
    ToolDefinition,
    ToolExecutionError,
    ToolLLMSpec,
    ToolParametersSchema,
    ToolPolicy,
    ToolRiskLevel,
)
from chat.application.tools.session_tools.tool_content_read.models import (
    ToolContentReadMode,
    ToolContentReadRequest,
    ToolContentReadResult,
    ToolContentSelector,
)
from chat.application.tools.session_tools.tool_content_read.service import ToolContentReadService
from chat.application.tools.tool_settings import tool_settings
from chat.application.utils.chunking_engine import UnitType

_UNIT_TYPE_ENUM = [unit_type.value for unit_type in UnitType]

PARAMETERS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "content_ids": {
            "type": "array",
            "items": {
                "type": "string",
                "minLength": 1,
            },
            "minItems": 1,
            "maxItems": 64,
            "description": (
                "Required. One or more cnt_* ids from previous <content_receipt> values. "
                "Large sets are automatically split into internal batches."
            ),
        },
        "mode": {
            "type": "string",
            "enum": ["ranked_expand", "regex_match"],
            "description": (
                "How to search cached content across one or more content_ids. "
                "Use ranked_expand for natural-language retrieval across documents, and "
                "regex_match for exact pattern matching across documents."
            ),
        },
        "selector": {
            "type": "object",
            "description": (
                "Optional chunk prefilter applied before ranked_expand or regex_match. "
                "Multiple selector groups are intersected."
            ),
            "properties": {
                "unit_types": {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "enum": _UNIT_TYPE_ENUM,
                        "description": "One structural unit type value from the chunking engine UnitType enum.",
                    },
                    "description": "Optional. Restrict search to chunks carrying these structural unit types.",
                },
                "sections": {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "minLength": 1,
                        "description": "One section name or section path fragment.",
                    },
                    "description": "Optional. Restrict search to matching section names or section path fragments.",
                },
                "pages": {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "minLength": 1,
                        "description": "One page label such as '3'.",
                    },
                    "description": "Optional. Restrict search to matching page labels when page metadata exists.",
                },
                "anchors": {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "minLength": 1,
                        "description": "One anchor name such as a table, figure, or equation label.",
                    },
                    "description": "Optional. Restrict search to matching anchor names.",
                },
                "chunk_indices": {
                    "type": "array",
                    "items": {
                        "type": "integer",
                        "description": "One chunk index within a content_id.",
                    },
                    "description": "Optional prefilter to restrict search to known chunk indices.",
                },
                "include_unknown": {
                    "type": "boolean",
                    "default": False,
                    "description": "Optional. When unit_types is used, keep chunks that do not carry unit_type metadata.",
                },
            },
        },
        "query": {
            "type": "string",
            "description": "Required for ranked_expand.",
        },
        "top_k": {
            "type": "integer",
            "default": 5,
            "description": "For ranked_expand. Maximum number of globally ranked matches.",
        },
        "pattern": {
            "type": "string",
            "description": "Required for regex_match.",
        },
        "max_matches": {
            "type": "integer",
            "default": 10,
            "description": "For regex_match. Maximum number of matches across all content_ids.",
        },
        "merge_before": {
            "type": "integer",
            "default": 0,
            "description": "For ranked_expand and regex_match. Number of chunks to include before each matched center chunk.",
        },
        "merge_after": {
            "type": "integer",
            "default": 0,
            "description": "For ranked_expand and regex_match. Number of chunks to include after each matched center chunk.",
        },
    },
    "required": ["content_ids", "mode"],
    "additionalProperties": False,
}

MAX_CONTENT_IDS = tool_settings.TOOL_CONTENT_READ_MAX_CONTENT_IDS


class ToolContentReadTool:
    """跨文档检索已有 cnt_* 内容。"""

    __slots__ = ("_service", "_definition")

    def __init__(
        self,
        *,
        content_store: ToolContentStore,
    ) -> None:
        self._service = ToolContentReadService(store=content_store)
        self._definition = ToolDefinition(
            llm_spec=ToolLLMSpec(
                name="tool_content_read",
                description=(
                    "Search focused windows from cached tool output across one or more content_ids.\n"
                    "\n"
                    "WHEN TO TRIGGER:\n"
                    "  - MUST trigger when a previous tool returned a <content_receipt> instead of full inline content.\n"
                    "  - SHOULD trigger when you need to find evidence across one or more cached documents.\n"
                    "DO NOT TRIGGER when:\n"
                    "  - You need sequential offset-based reading from one content_id — use tool_content_sequential_read instead.\n"
                    "  - You need new content from the web — use web_fetch or web_crawl instead.\n"
                    "\n"
                    "INPUT RULES:\n"
                    "  - content_ids MUST be cnt_* ids from previous content receipts; large sets are auto-batched internally.\n"
                    "  - mode MUST be one of: ranked_expand (semantic search), regex_match (exact pattern).\n"
                    "  - query is required for ranked_expand; pattern is required for regex_match.\n"
                    "  - selector optionally prefilters chunks by unit_types, sections, pages, anchors, or chunk_indices.\n"
                    "  - merge_before/merge_after expand windows around center chunks.\n"
                    "\n"
                    "OUTPUT RULES:\n"
                    "  - Returns globally ordered matches across content_ids, each carrying its content_id and readable window.\n"
                    "  - This tool reads existing cnt_* content and never creates another content receipt.\n"
                ),
                parameters_schema=ToolParametersSchema(PARAMETERS_SCHEMA),
            ),
            policy=ToolPolicy(
                expose_by_default=True,
                persist_output=True,
                risk_level=ToolRiskLevel.LOW,
                required_context_keys=("session_id",),
                timeout_seconds=tool_settings.TOOL_CONTENT_READ_TIMEOUT_SECONDS,
            ),
        )

    @property
    def definition(self) -> ToolDefinition:
        return self._definition

    async def execute(self, context: dict[str, Any], **kwargs: Any) -> ToolContentReadResult:
        session_id = context["session_id"]

        try:
            selector_payload = kwargs.get("selector") or {}
            selector = ToolContentSelector(
                unit_types=tuple(selector_payload.get("unit_types") or ()),
                sections=tuple(selector_payload.get("sections") or ()),
                pages=tuple(selector_payload.get("pages") or ()),
                anchors=tuple(selector_payload.get("anchors") or ()),
                chunk_indices=tuple(int(value) for value in (selector_payload.get("chunk_indices") or ())),
                include_unknown=bool(selector_payload.get("include_unknown", False)),
            )
            request = ToolContentReadRequest(
                content_ids=tuple(str(value) for value in kwargs["content_ids"]),
                mode=ToolContentReadMode(str(kwargs["mode"])),
                selector=selector,
                query=kwargs.get("query"),
                top_k=int(kwargs.get("top_k") or 5),
                pattern=kwargs.get("pattern"),
                max_matches=int(kwargs.get("max_matches") or 10),
                merge_before=int(kwargs.get("merge_before") or 0),
                merge_after=int(kwargs.get("merge_after") or 0),
            )

            batch_size = max(1, int(MAX_CONTENT_IDS))
            all_matches = []
            all_failed = []
            for batch_content_ids in batched(request.content_ids, batch_size=batch_size):
                batch_result = await self._service.read(
                    request=ToolContentReadRequest(
                        content_ids=batch_content_ids,
                        mode=request.mode,
                        selector=request.selector,
                        query=request.query,
                        top_k=request.top_k,
                        pattern=request.pattern,
                        max_matches=request.max_matches,
                        merge_before=request.merge_before,
                        merge_after=request.merge_after,
                    ),
                    session_id=session_id,
                )
                all_matches.extend(batch_result.matches)
                all_failed.extend(batch_result.failed)

            return ToolContentReadResult(
                mode=request.mode,
                matches=tuple(all_matches),
                failed=tuple(all_failed),
            )
        except ToolExecutionError:
            raise
        except Exception as exc:
            raise ToolExecutionError(
                reason="tool_content_read_failed",
                detail_reason=str(exc),
                retryable=False,
            ) from exc
