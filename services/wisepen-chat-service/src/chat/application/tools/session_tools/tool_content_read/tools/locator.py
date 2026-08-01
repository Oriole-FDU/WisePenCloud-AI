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

from ..services.models import ToolContentLocatorReadResult
from ..services.reader import ToolContentReader

_TIMEOUT_SECONDS = 300.0
_PARAMETERS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "content_id": {
            "type": "string",
            "minLength": 1,
            "description": "One cnt_* id from a previous content receipt.",
        },
        "locator": {
            "type": "string",
            "minLength": 1,
            "description": (
                "Exact locator name returned by ranked read or another read, such as page:12, "
                "section:Methods > Dataset, anchor:Figure 3, or anchor:Table 2."
            ),
        },
    },
    "required": ["content_id", "locator"],
    "additionalProperties": False,
}


class ToolContentReadByLocatorTool:
    """按命名 locator 直接读取权威原文。"""

    __slots__ = ("_definition", "_reader")

    def __init__(self, *, reader: ToolContentReader) -> None:
        self._reader = reader
        self._definition = ToolDefinition(
            llm_spec=ToolLLMSpec(
                name="tool_content_read_by_locator",
                description=(
                    "Read an exact named page, Markdown section, figure, table, or equation locator "
                    "from cached source content. This bypasses retrieval chunks and slices the original "
                    "text using the locator's authoritative offsets. Use locator names exposed by "
                    "tool_content_ranked_read or previous read results. Repeated locator names return all "
                    "matching source ranges."
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
    ) -> ToolContentLocatorReadResult:
        del config
        try:
            return await self._reader.read_locator(
                content_id=str(kwargs["content_id"]),
                session_id=str(context["session_id"]),
                locator_name=str(kwargs["locator"]).strip(),
            )
        except Exception as exc:
            raise ToolExecutionError(
                reason="tool_content_read_by_locator_failed",
                detail_reason=str(exc),
                retryable=False,
            ) from exc
