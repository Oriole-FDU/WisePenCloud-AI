from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from common.utils.markdown import OutlineFormatter

from chat.application.tools.core import (
    ToolDefinition,
    ToolExecutionError,
    ToolLLMSpec,
    ToolParametersSchema,
    ToolPolicy,
    ToolRiskLevel,
    ToolSelectionMode,
    ToolUISpec,
)
from chat.application.tools.core.output_cache.cache_store import get_tool_content

_TIMEOUT_SECONDS = 300.0
_PARAMETERS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "content_id": {
            "type": "string",
            "minLength": 1,
            "description": (
                "Required. One cached tool output content_id returned in a "
                "previous tool result."
            ),
        },
    },
    "required": ["content_id"],
    "additionalProperties": False,
}


@dataclass(slots=True)
class ToolOutputStructureResult:
    """结构目录及其原文定位信息。"""

    content_id: str
    total_length: int
    outline: str


class InspectToolOutputStructureTool:
    def __init__(self) -> None:
        self._definition = ToolDefinition(
            llm_spec=ToolLLMSpec(
                name="inspect_tool_output_structure",
                description=(
                    "Get a compact section outline for one cached tool output "
                    "without reading body text.\n\n"
                    "The outline is a heuristic navigation preview, not an authoritative "
                    "table of contents. It may be incomplete, noisy, or have incorrect "
                    "hierarchy or labels. Anchor labels are approximate "
                    "navigation hints, not verified facts.\n\n"
                    "Each outline row ends with id=<section_id> @<char_offset>; the "
                    "value after @ is that section's absolute character offset in the "
                    "cached source text (Python slice semantics), and the next row's "
                    "@offset roughly bounds this section's content.\n\n"
                    "Read the actual content in either way:\n"
                    "  - Pass id=<section_id> to read_tool_output_section for a whole "
                    "section; paginate long sections with start_offset=next_offset "
                    "while has_more is true.\n"
                    "  - Pass @<char_offset> to read_tool_output_range as start, with "
                    "the next row's @offset as an optional end, to read an exact "
                    "bounded window when the heuristic section boundaries look wrong.\n\n"
                    "Treat the outline as a soft prior for navigation only. Do not infer "
                    "that a section is absent solely from this outline. If the outline "
                    "conflicts with the retrieved body text, trust the body text."
                ),
                parameters_schema=ToolParametersSchema(_PARAMETERS_SCHEMA),
            ),
            policy=ToolPolicy(
                expose_by_default=False,
                selection_mode=ToolSelectionMode.CONTEXTUAL,
                persist_output=True,
                risk_level=ToolRiskLevel.LOW,
                required_context_keys=("session_id",),
                timeout_seconds=_TIMEOUT_SECONDS,
            ),
            ui_spec=ToolUISpec(
                display_name="查看缓存的工具输出结构",
                description="读取缓存工具输出的精简章节目录，用于后续确定性读取。",
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
    ) -> ToolOutputStructureResult:
        del config
        try:
            content_id = kwargs["content_id"]
            stored = await get_tool_content(
                content_id=content_id,
                session_id=context["session_id"],
            )
            if stored is None:
                raise ToolExecutionError(
                    reason="cached_tool_output_not_found",
                    retryable=False,
                )

            result = ToolOutputStructureResult(
                content_id=content_id,
                total_length=len(stored.text),
                outline=OutlineFormatter(
                    sections=stored.sections,
                    anchors=stored.anchors,
                ).global_outline(),
            )

            return result
        except Exception as exc:
            raise ToolExecutionError(
                reason="inspect_cached_tool_output_structure_failed",
                detail_reason=str(exc),
                retryable=False,
            ) from exc
