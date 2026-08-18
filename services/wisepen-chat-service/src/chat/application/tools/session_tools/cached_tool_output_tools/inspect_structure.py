from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from common.utils.document import OutlineAssembler, OutlineNode

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
from chat.application.tools.core.output_cache.cache_store import (
    ToolContentStore as CachedToolOutputStore,
)

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
class CachedToolOutputStructureResult:
    """缓存正文的模型可见目录；内部 offset/path 不跨越工具边界。"""

    content_id: str
    total_length: int | None = None
    outline: list[OutlineNode] = field(default_factory=list)
    reason: str | None = None


class CachedToolOutputInspectStructureTool:
    __slots__ = ("_definition", "_store")

    def __init__(self, *, store: CachedToolOutputStore) -> None:
        self._store = store
        self._definition = ToolDefinition(
            llm_spec=ToolLLMSpec(
                name="inspect_cached_tool_output_structure",
                description=(
                    "Get a compact section outline for one cached tool output "
                    "without reading body text.\n\n"
                    "Use outline[].section_id with "
                    "read_cached_tool_output_by_section. Page ranges and anchor "
                    "labels are navigation hints attached to each outline node."
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
    ) -> CachedToolOutputStructureResult:
        del config
        try:
            content_id = str(kwargs["content_id"])
            stored = await self._store.get(
                content_id=content_id,
                session_id=str(context["session_id"]),
            )
            if stored is None:
                return CachedToolOutputStructureResult(
                    content_id=content_id,
                    reason="cached_tool_output_not_found",
                )
            return CachedToolOutputStructureResult(
                content_id=content_id,
                total_length=len(stored.text),
                outline=OutlineAssembler.assemble(
                    sections=stored.sections,
                    pages=stored.pages,
                    anchors=stored.anchors,
                ),
            )
        except Exception as exc:
            raise ToolExecutionError(
                reason="inspect_cached_tool_output_structure_failed",
                detail_reason=str(exc),
                retryable=False,
            ) from exc
