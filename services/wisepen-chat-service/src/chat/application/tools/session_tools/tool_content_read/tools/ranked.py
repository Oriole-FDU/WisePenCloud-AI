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

from ..services.models import ToolContentRankedReadRequest, ToolContentRankedReadResult
from ..services.reader import ToolContentReader

_TIMEOUT_SECONDS = 300.0
_CONTENT_IDS_SCHEMA: dict[str, Any] = {
    "type": "array",
    "items": {"type": "string", "minLength": 1},
    "minItems": 1,
    "maxItems": 64,
    "description": "One or more cnt_* ids from previous contents entries.",
}
_PARAMETERS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "content_ids": _CONTENT_IDS_SCHEMA,
        "query": {
            "type": "string",
            "minLength": 1,
            "description": "Complete question the ranked source chunks should answer.",
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


class ToolContentRankedReadTool:
    """跨缓存内容排序并读取语义 chunks。"""

    __slots__ = ("_definition", "_reader")

    def __init__(self, *, reader: ToolContentReader) -> None:
        self._reader = reader
        self._definition = ToolDefinition(
            llm_spec=ToolLLMSpec(
                name="tool_content_ranked_read",
                description=(
                    "Rank semantic chunks from one or more cached contents and read the most "
                    "relevant source spans. Chunks follow Markdown section semantics rather than "
                    "physical page boundaries. Each result includes known page, section, and anchor "
                    "locator names for deterministic follow-up reads. Use tool_content_read with "
                    "locator for a complete located structure, regex read for exact patterns, or "
                    "start/end offsets for an already known range."
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
    ) -> ToolContentRankedReadResult:
        del config
        query = str(kwargs.get("query") or "").strip()
        if not query:
            raise ToolExecutionError(reason="missing_query", detail_reason="query is required.")
        try:
            return await self._reader.read_ranked(
                request=ToolContentRankedReadRequest(
                    content_ids=tuple(str(value) for value in kwargs["content_ids"]),
                    query=query,
                    top_k=max(int(kwargs.get("top_k", 10)), 0),
                ),
                session_id=str(context["session_id"]),
            )
        except Exception as exc:
            raise ToolExecutionError(
                reason="tool_content_ranked_read_failed",
                detail_reason=str(exc),
                retryable=False,
            ) from exc
