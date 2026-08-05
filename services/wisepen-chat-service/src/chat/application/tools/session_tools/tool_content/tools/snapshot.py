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
from ..services.service import ToolContentService

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

    __slots__ = ("_definition", "_service")

    def __init__(self, *, service: ToolContentService) -> None:
        self._service = service
        self._definition = ToolDefinition(
            llm_spec=ToolLLMSpec(
                name="tool_content_get_snapshot",
                description=(
                    "Get the structure snapshot for one cached content_id without reading body text.\n\n"
                    "WHEN TO TRIGGER:\n"
                    "  - MUST trigger when you need available pages, sections, anchors, or offsets "
                    "before choosing what to read.\n"
                    "  - SHOULD trigger before read tools when you know the document but not the "
                    "exact page label, section path, or offset.\n\n"
                    "OUTPUT RULES:\n"
                    "  - Returns total_length, pages, a section tree, and anchors.\n"
                    "  - Use page_label with tool_content_read_pages.\n"
                    "  - Use section_path with tool_content_read_sections.\n"
                    "  - Use offsets with tool_content_read_range.\n"
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
            return await self._service.get_snapshot(
                content_id=str(kwargs["content_id"]),
                session_id=str(context["session_id"]),
            )
        except Exception as exc:
            raise ToolExecutionError(
                reason="tool_content_get_snapshot_failed",
                detail_reason=str(exc),
                retryable=False,
            ) from exc
