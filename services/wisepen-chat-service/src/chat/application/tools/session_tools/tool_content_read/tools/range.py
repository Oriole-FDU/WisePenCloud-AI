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

from ..services.models import ToolContentReadResult
from ..services.reader import ToolContentReader

_TIMEOUT_SECONDS = 300.0
_DEFAULT_READ_CHARS = 8000
_PARAMETERS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "content_id": {
            "type": "string",
            "minLength": 1,
            "description": "Required. One cnt_* id from a previous contents entry.",
        },
        "start": {
            "type": "integer",
            "description": (
                "Optional inclusive character offset. Negative values count from the end."
            ),
        },
        "end": {
            "type": "integer",
            "description": (
                "Optional exclusive character offset. Negative values count from the end."
            ),
        },
    },
    "required": ["content_id"],
    "additionalProperties": False,
}


class ToolContentReadTool:
    """读取单文档的任意字符区间。"""

    __slots__ = ("_definition", "_reader")

    def __init__(self, *, reader: ToolContentReader) -> None:
        self._reader = reader
        self._definition = ToolDefinition(
            llm_spec=ToolLLMSpec(
                name="tool_content_read",
                description=(
                    "Read any character range from one cached content_id.\n\n"
                    "WHEN TO TRIGGER:\n"
                    "  - MUST trigger when you need a known range, the beginning, or the end of cached content.\n"
                    "  - SHOULD trigger when nearby context matters more than cross-document search.\n"
                    "DO NOT TRIGGER when:\n"
                    "  - You need ranked semantic retrieval across documents; use tool_content_ranked_read.\n"
                    "  - You need exact pattern matching across documents; use tool_content_regex_read.\n\n"
                    "INPUT RULES:\n"
                    "  - Ranges use Python slice semantics: start is inclusive and end is exclusive.\n"
                    "  - Negative offsets count from the end; start=-1000 reads the final 1000 characters.\n"
                    "  - start=0,end=2000 reads the first 2000 characters.\n"
                    "  - Omitting both offsets reads the first 8000 characters; content beyond this range is clipped.\n\n"
                    "  - Use total_length from the source contents entry to choose a strategy: read all when it is "
                    "under 8000, read in two ranges when it is 8000-16000, and prefer "
                    "tool_content_ranked_read before focused range reads when it exceeds 16000.\n\n"
                    "OUTPUT RULES:\n"
                    "  - Returns the requested text with normalized absolute offsets and structural locators.\n"
                    "  - This tool reads existing cnt_* content and never creates another content entry."
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
    ) -> ToolContentReadResult:
        del config
        start = int(kwargs["start"]) if "start" in kwargs else None
        end = int(kwargs["end"]) if "end" in kwargs else None
        if start is None and end is None:
            end = _DEFAULT_READ_CHARS

        try:
            return await self._reader.read_range(
                content_id=str(kwargs["content_id"]),
                session_id=str(context["session_id"]),
                start=start,
                end=end,
            )
        except Exception as exc:
            raise ToolExecutionError(
                reason="tool_content_read_failed",
                detail_reason=str(exc),
                retryable=False,
            ) from exc
