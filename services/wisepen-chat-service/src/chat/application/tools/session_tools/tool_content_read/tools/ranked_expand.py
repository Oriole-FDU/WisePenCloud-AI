from __future__ import annotations

from dataclasses import replace
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
from chat.application.tools.utils.batching import batched
from chat.application.utils.ranking.pipeline import RankingPipeline

from ..services.content_loader import ToolContentLoader
from ..services.content_window_builder import ToolContentWindowBuilder
from ..services.models import (
    ToolContentReadFailure,
    ToolContentRankedExpandReadRequest,
    ToolContentRankedExpandReadResult,
    ToolContentSelector,
)
from ..services.readers import RankedExpandReader
from .common import (
    CONTENT_IDS_SCHEMA,
    SELECTOR_SCHEMA,
)

_INTERNAL_BATCH_SIZE = 16
_TIMEOUT_SECONDS = 300.0
_PARAMETERS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "content_ids": CONTENT_IDS_SCHEMA,
        "selector": SELECTOR_SCHEMA,
        "query": {
            "type": "string",
            "minLength": 1,
            "description": "Required. Natural-language query used to rank candidate chunks.",
        },
        "top_k": {
            "type": "integer",
            "default": 10,
            "description": "Maximum number of globally ranked windows returned.",
        },
        "merge_before": {"type": "integer", "default": 0},
        "merge_after": {"type": "integer", "default": 0},
    },
    "required": ["content_ids", "query"],
    "additionalProperties": False,
}


class ToolContentRankedExpandReadTool:
    """跨文档重排检索已有 cnt_* 内容。"""

    __slots__ = ("_definition", "_reader")

    def __init__(
            self,
            *,
            content_store: ToolContentStore,
            ranking_pipeline: RankingPipeline,
            max_window_chars: int | None = None,
    ) -> None:
        self._reader = RankedExpandReader(
            loader=ToolContentLoader(store=content_store),
            ranking_pipeline=ranking_pipeline,
            window_builder=ToolContentWindowBuilder(max_chars=max_window_chars),
        )
        self._definition = ToolDefinition(
            llm_spec=ToolLLMSpec(
                name="tool_content_ranked_expand_read",
                description=(
                    "Rank and expand focused windows from cached tool output across one or more content_ids.\n\n"
                    "WHEN TO TRIGGER:\n"
                    "  - MUST trigger when previous tool calls returned content_receipts and natural-language retrieval is needed.\n"
                    "  - SHOULD trigger when answer-relevant evidence must be found across cached documents.\n"
                    "DO NOT TRIGGER when:\n"
                    "  - You need exact pattern matching; use tool_content_regex_read.\n"
                    "  - You need sequential offset-based reading; use tool_content_sequential_read.\n\n"
                    "INPUT RULES:\n"
                    "  - Accepts up to 64 content_ids and reads them in bounded internal batches of 16.\n"
                    "  - query describes the evidence to retrieve.\n"
                    "  - selector prefilters chunks before ranking; selector groups are intersected.\n"
                    "  - merge_before and merge_after expand windows around ranked chunks.\n\n"
                    "OUTPUT RULES:\n"
                    "  - Returns ranked windows with rank and score, plus per-content failures.\n"
                    "  - Successful batches are retained when another internal batch fails.\n"
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
    ) -> ToolContentRankedExpandReadResult:
        query = str(kwargs.get("query") or "").strip()
        if not query:
            raise ToolExecutionError(reason="missing_query", detail_reason="query is required.")

        content_ids = tuple(str(value) for value in kwargs["content_ids"])
        top_k = max(int(kwargs.get("top_k", 10)), 0)
        ranked = []
        failed = []
        for batch in batched(content_ids, batch_size=_INTERNAL_BATCH_SIZE):
            request = ToolContentRankedExpandReadRequest(
                content_ids=batch,
                query=query,
                selector=ToolContentSelector.from_payload(kwargs.get("selector")),
                top_k=top_k,
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
            ranked.extend(result.ranked)
            failed.extend(result.failed)

        globally_ranked = tuple(
            replace(item, rank=rank)
            for rank, item in enumerate(
                sorted(ranked, key=lambda item: (-item.score, item.rank)),
                start=1,
            )
        )
        return ToolContentRankedExpandReadResult(
            ranked=globally_ranked[:top_k],
            failed=tuple(failed),
        )
