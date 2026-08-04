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

from ..services.models import ToolContentLocatorReadResult, ToolContentReadResult
from ..services.reader import ToolContentReader

_TIMEOUT_SECONDS = 300.0
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
        "locator": {
            "type": "string",
            "minLength": 1,
            "description": (
                "Optional exact locator name from tool_content_get_snapshot or ranked read, such as "
                "page:12, section:Methods > Dataset, anchor:Figure 3, or anchor:Table 2."
            ),
        },
    },
    "required": ["content_id"],
    "additionalProperties": False,
}


class ToolContentReadTool:
    """按 locator 或 offset 区间读取单文档权威原文。"""

    __slots__ = ("_definition", "_reader")

    def __init__(self, *, reader: ToolContentReader) -> None:
        self._reader = reader
        self._definition = ToolDefinition(
            llm_spec=ToolLLMSpec(
                name="tool_content_read",
                description=(
                    "Read source text from one cached content_id by either locator or character range.\n\n"
                    "WHEN TO TRIGGER:\n"
                    "  - MUST trigger when you need a known page/section/anchor locator, a known range, "
                    "the beginning, or the end of cached content.\n"
                    "  - SHOULD trigger when nearby source context matters more than cross-document search.\n"
                    "DO NOT TRIGGER when:\n"
                    "  - You need ranked semantic retrieval across documents; use tool_content_ranked_read.\n"
                    "  - You need exact pattern matching across documents; use tool_content_regex_read.\n\n"
                    "  - You need the locator list before choosing text; use tool_content_get_snapshot.\n\n"
                    "INPUT RULES:\n"
                    "  - Provide locator OR start/end offsets, not both.\n"
                    "  - locator must be an exact name already exposed by tool_content_get_snapshot or "
                    "ranked results, such as "
                    "page:12 or section:Methods > Dataset.\n"
                    "  - Ranges use Python slice semantics: start is inclusive and end is exclusive.\n"
                    "  - Negative offsets count from the end; start=-1000 reads the final 1000 characters.\n"
                    "  - start=0,end=2000 reads the first 2000 characters.\n"
                    "  - Omitting both offsets reads a token-budgeted window from the beginning.\n"
                    "  - If a requested range is truncated, continue from the returned end_offset.\n"
                    "  - For long sources, prefer ranked or regex reads before requesting focused ranges.\n\n"
                    "OUTPUT RULES:\n"
                    "  - Returns the requested text with normalized absolute offsets.\n"
                    "  - Repeated locator names return matching ranges within one shared budget.\n"
                    "  - budget_exhausted is true when more matching ranges remain.\n"
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
    ) -> ToolContentReadResult | ToolContentLocatorReadResult:
        del config
        has_locator = "locator" in kwargs and str(kwargs["locator"]).strip()
        has_range = "start" in kwargs or "end" in kwargs
        if has_locator and has_range:
            raise ToolExecutionError(
                reason="invalid_tool_content_read_request",
                detail_reason="locator cannot be combined with start/end.",
                retryable=False,
            )
        if has_locator:
            try:
                return await self._reader.read_locator(
                    content_id=str(kwargs["content_id"]),
                    session_id=str(context["session_id"]),
                    locator_name=str(kwargs["locator"]).strip(),
                )
            except Exception as exc:
                raise ToolExecutionError(
                    reason="tool_content_read_failed",
                    detail_reason=str(exc),
                    retryable=False,
                ) from exc

        start = int(kwargs["start"]) if "start" in kwargs else None
        end = int(kwargs["end"]) if "end" in kwargs else None

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
