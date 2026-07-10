from __future__ import annotations

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
    ToolContentRerankReadRequest,
    ToolContentReadResult,
)
from chat.application.tools.session_tools.tool_content_read.content_loader import (
    ToolContentLoader,
)
from chat.application.tools.session_tools.tool_content_read.content_window_builder import (
    ToolContentWindowBuilder,
)
from chat.application.tools.session_tools.tool_content_read.readers import (
    RankedExpandReader,
)
from chat.application.utils.ranking_engine.registry import get_ranking_engine


MAX_CONTENT_IDS = 16
TOOL_CONTENT_READ_TIMEOUT_SECONDS = 300.0
DEFAULT_TOP_K = 10

PARAMETERS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "content_ids": CONTENT_IDS_SCHEMA,
        "selector": SELECTOR_SCHEMA,
        "query": {
            "type": "string",
            "minLength": 1,
            "description": "Required. Natural-language query used to rerank candidate chunks.",
        },
        "top_k": {
            "type": "integer",
            "default": DEFAULT_TOP_K,
            "description": "Maximum number of globally reranked matches.",
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
    "required": ["content_ids", "query"],
    "additionalProperties": False,
}


class ToolContentRerankReadTool:
    """跨文档重排检索已有 cnt_* 内容。"""

    __slots__ = ("_definition", "_reader")

    def __init__(
            self,
            *,
            content_store: ToolContentStore,
            max_window_chars: int | None = None,
    ) -> None:
        self._reader = RankedExpandReader(
            loader=ToolContentLoader(store=content_store),
            ranking_engine=get_ranking_engine("read.ranked_expand"),
            window_builder=ToolContentWindowBuilder(max_window_chars=max_window_chars),
        )
        self._definition = ToolDefinition(
            llm_spec=ToolLLMSpec(
                name="tool_content_rerank_read",
                description=(
                    "Rerank focused windows from cached tool output across one or more content_ids.\n"
                    "\n"
                    "WHEN TO TRIGGER:\n"
                    "  - MUST trigger when a previous tool returned a <content_receipt> and you need natural-language retrieval.\n"
                    "  - SHOULD trigger when you need answer-relevant evidence across one or more cached documents.\n"
                    "DO NOT TRIGGER when:\n"
                    "  - You need exact pattern matching — use tool_content_regex_read instead.\n"
                    "  - You need sequential offset-based reading from one content_id — use tool_content_sequential_read instead.\n"
                    "  - You need new content from the web — use web_fetch or web_crawl instead.\n"
                    "\n"
                    "INPUT RULES:\n"
                    "  - content_ids MUST be cnt_* ids from previous content receipts; multiple ids are read in bounded internal batches.\n"
                    "  - query is required and is used to rerank candidate chunks.\n"
                    "  - selector optionally prefilters chunks by block_kinds, sections, page_labels, anchor_labels, or chunk_indices.\n"
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
                timeout_seconds=TOOL_CONTENT_READ_TIMEOUT_SECONDS,
            ),
        )

    @property
    def definition(self) -> ToolDefinition:
        return self._definition

    async def execute(self, context: dict[str, Any], **kwargs: Any) -> ToolContentReadResult:
        query = str(kwargs.get("query") or "").strip()
        if not query:
            raise ToolExecutionError(
                reason="missing_query",
                detail_reason="query is required.",
                retryable=False,
            )

        request = ToolContentRerankReadRequest(
            content_ids=tuple(str(value) for value in kwargs["content_ids"]),
            query=query,
            selector=selector_from_payload(kwargs.get("selector")),
            top_k=int(kwargs.get("top_k") or DEFAULT_TOP_K),
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
                reason="tool_content_rerank_read_failed",
                detail_reason=str(exc),
                retryable=False,
            ) from exc

