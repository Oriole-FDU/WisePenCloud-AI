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

from ..services.models import ToolContentWindow
from ..services.reader import ToolContentReader

_TIMEOUT_SECONDS = 300.0
_DEFAULT_READ_CHARS = 4000
_PARAMETERS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "content_id": {
            "type": "string",
            "minLength": 1,
            "description": "A cached cnt_* content ID returned by an earlier tool call.",
        },
        "start": {
            "type": "integer",
            "description": (
                "Inclusive character offset where reading begins. Negative values count from "
                "the end; use the previous end_offset to continue reading."
            ),
        },
        "end": {
            "type": "integer",
            "description": (
                "Exclusive character offset where reading stops. Negative values count from "
                "the end. Omit it to read forward from start up to the output limit."
            ),
        },
    },
    "required": ["content_id"],
    "additionalProperties": False,
}


class ToolContentReadTool:
    """读取单文档的任意字符区间。"""

    __slots__ = ("_definition", "_reader")

    def __init__(
            self,
            *,
            reader: ToolContentReader,
    ) -> None:
        self._reader = reader
        self._definition = ToolDefinition(
            llm_spec=ToolLLMSpec(
                name="tool_content_read",
                description=(
                    "Read an exact character range from one cached cnt_* content. Use this when "
                    "you know the desired offsets, need the beginning or end, or want to continue "
                    "from a previous end_offset. Use tool_content_regex_read when you know an exact "
                    "pattern but not its location, and tool_content_ranked_expand_read when you know "
                    "the question but not the wording or location. Ranges follow Python slice "
                    "semantics: start is inclusive, end is exclusive, and negative offsets count "
                    "from the end. Omitting both offsets returns the first 4000 characters. The "
                    "result includes normalized absolute offsets and available structural locators; "
                    "large ranges are capped by the output limit."
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
    ) -> ToolContentWindow:
        start = int(kwargs["start"]) if "start" in kwargs else None
        end = int(kwargs["end"]) if "end" in kwargs else None
        if start is None and end is None:
            end = _DEFAULT_READ_CHARS

        try:
            window = await self._reader.read_range(
                content_id=str(kwargs["content_id"]),
                session_id=str(context["session_id"]),
                start=start,
                end=end,
            )
            if window is None:
                raise ToolExecutionError(reason="content_not_found")
            return window
        except Exception as exc:
            if isinstance(exc, ToolExecutionError):
                raise
            raise ToolExecutionError(
                reason="tool_content_read_failed",
                detail_reason=str(exc),
                retryable=False,
            ) from exc
