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

from ..tool_content_read.content_loader import ToolContentLoader
from ..tool_content_read.content_window_builder import ToolContentWindowBuilder
from ..tool_content_read.models import ToolContentSequentialReadResult
from ..tool_content_read.readers import SequentialReader

_TIMEOUT_SECONDS = 300.0
_PARAMETERS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "content_id": {
            "type": "string",
            "minLength": 1,
            "description": "Required. One cnt_* id from a previous content receipt.",
        },
        "offset": {
            "type": "integer",
            "default": 0,
            "description": "Optional character offset to start reading from.",
        },
        "limit": {
            "type": "integer",
            "default": 4000,
            "description": "Optional maximum number of characters to read.",
        },
    },
    "required": ["content_id"],
    "additionalProperties": False,
}


class ToolContentSequentialReadTool:
    """单文档顺序读取工具。"""

    __slots__ = ("_definition", "_reader")

    def __init__(
            self,
            *,
            content_store: ToolContentStore,
            max_window_chars: int | None = None,
    ) -> None:
        self._reader = SequentialReader(
            loader=ToolContentLoader(store=content_store),
            window_builder=ToolContentWindowBuilder(max_chars=max_window_chars),
        )
        self._definition = ToolDefinition(
            llm_spec=ToolLLMSpec(
                name="tool_content_sequential_read",
                description=(
                    "Read one cached content_id sequentially by offset.\n\n"
                    "WHEN TO TRIGGER:\n"
                    "  - MUST trigger when you need to continue reading a single cached content from a known offset.\n"
                    "  - SHOULD trigger when nearby context matters more than cross-document search.\n"
                    "DO NOT TRIGGER when:\n"
                    "  - You need natural-language retrieval across documents; use tool_content_ranked_expand_read.\n"
                    "  - You need exact pattern matching across documents; use tool_content_regex_read.\n\n"
                    "OUTPUT RULES:\n"
                    "  - Returns one readable window with offsets and available structural locators.\n"
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
    ) -> ToolContentSequentialReadResult:
        try:
            return await self._reader.read(
                content_id=str(kwargs["content_id"]),
                session_id=str(context["session_id"]),
                offset=int(kwargs.get("offset", 0)),
                limit=int(kwargs.get("limit", 4000)),
            )
        except Exception as exc:
            raise ToolExecutionError(
                reason="tool_content_sequential_read_failed",
                detail_reason=str(exc),
                retryable=False,
            ) from exc
