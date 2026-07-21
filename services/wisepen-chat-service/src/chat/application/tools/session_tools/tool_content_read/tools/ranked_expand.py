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
from chat.application.tools.utils.batching import batched

from ..services.models import (
    ToolContentRankedExpandItem,
    ToolContentRankedExpandReadRequest,
    ToolContentSelector,
)
from ..services.reader import ToolContentReader
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
            "description": (
                "The complete question the cached contents should answer, for example: "
                "'What is 2+2?'. Ask the question directly instead of passing keywords or "
                "describing the retrieval task."
            ),
        },
        "top_k": {
            "type": "integer",
            "default": 10,
            "description": "Maximum number of answer-relevant windows returned across all content_ids.",
        },
        "merge_before": {
            "type": "integer",
            "default": 0,
            "description": "Number of adjacent chunks before each relevant chunk to include as context.",
        },
        "merge_after": {
            "type": "integer",
            "default": 0,
            "description": "Number of adjacent chunks after each relevant chunk to include as context.",
        },
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
            reader: ToolContentReader,
    ) -> None:
        self._reader = reader
        self._definition = ToolDefinition(
            llm_spec=ToolLLMSpec(
                name="tool_content_ranked_expand_read",
                description=(
                    "Retrieve answer-relevant passages from one or more cached cnt_* contents. Use "
                    "this when you can state the question but do not know the exact wording or location "
                    "of the evidence. Use tool_content_regex_read when the exact text pattern is known, "
                    "and tool_content_read when character offsets are known. Write query as the complete "
                    "question the candidate passages should answer, not as keywords or retrieval "
                    "instructions. Optional selectors narrow the candidate chunks before ranking, and "
                    "merge_before or merge_after adds neighboring chunks for context. Results are "
                    "returned in descending relevance order."
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
    ) -> tuple[ToolContentRankedExpandItem, ...]:
        query = str(kwargs.get("query") or "").strip()
        if not query:
            raise ToolExecutionError(reason="missing_query", detail_reason="query is required.")

        content_ids = tuple(str(value) for value in kwargs["content_ids"])
        top_k = max(int(kwargs.get("top_k", 10)), 0)
        ranked = []
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
                result = await self._reader.read_ranked_expand(
                    request=request,
                    session_id=str(context["session_id"]),
                )
            except Exception:
                continue
            ranked.extend(result)

        ranked.sort(key=lambda candidate: -candidate.score)
        return tuple(
            candidate.item
            for candidate in ranked[:top_k]
        )
