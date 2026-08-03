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

from ..services.models import ToolContentSnapshotResult
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
    },
    "required": ["content_id"],
    "additionalProperties": False,
}


class ToolContentGetSnapshotTool:
    """读取缓存正文的结构快照，不返回正文。"""

    __slots__ = ("_definition", "_reader")

    def __init__(self, *, reader: ToolContentReader) -> None:
        self._reader = reader
        self._definition = ToolDefinition(
            llm_spec=ToolLLMSpec(
                name="tool_content_get_snapshot",
                description=(
                    "Get the locator snapshot for one cached content_id without reading body text.\n\n"
                    "WHEN TO TRIGGER:\n"
                    "  - MUST trigger when you need the available page, section, figure, table, "
                    "or equation locators before choosing what to read.\n"
                    "  - SHOULD trigger before tool_content_read when you know the document but not "
                    "the exact locator or offset.\n\n"
                    "OUTPUT RULES:\n"
                    "  - Returns total_length and locators with exact names, kinds, and offsets.\n"
                    "  - Use a returned locator name with tool_content_read(locator=...).\n"
                    "  - This tool does not return body text."
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
    ) -> ToolContentSnapshotResult:
        del config
        try:
            return await self._reader.get_snapshot(
                content_id=str(kwargs["content_id"]),
                session_id=str(context["session_id"]),
            )
        except Exception as exc:
            raise ToolExecutionError(
                reason="tool_content_get_snapshot_failed",
                detail_reason=str(exc),
                retryable=False,
            ) from exc
