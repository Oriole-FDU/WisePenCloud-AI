from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from common.utils.document import Section

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
    StoredToolContent as StoredCachedToolOutput,
)
from chat.application.tools.core.output_cache.cache_store import (
    ToolContentStore as CachedToolOutputStore,
)
from chat.application.tools.session_tools.cached_tool_output_tools.window import (
    CachedToolOutputWindow,
    CachedToolOutputWindowBuilder,
)
from chat.core.config.app_settings import settings

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
        "section_ids": {
            "type": "array",
            "items": {"type": "string", "minLength": 1},
            "minItems": 1,
            "maxItems": 20,
            "description": (
                "Exact section_id values returned by "
                "inspect_cached_tool_output_structure."
            ),
        },
    },
    "required": ["content_id", "section_ids"],
    "additionalProperties": False,
}


@dataclass(slots=True)
class CachedToolOutputReadBySectionItem:
    """按章节读取结果；section_path 是该 section_id 对应的可读标题路径。"""

    section_id: str
    section_path: str | None = None
    windows: list[CachedToolOutputWindow] = field(default_factory=list)
    reason: str | None = None


@dataclass(slots=True)
class CachedToolOutputReadBySectionResult:
    content_id: str
    items: list[CachedToolOutputReadBySectionItem] = field(default_factory=list)
    budget_exhausted: bool = False


class CachedToolOutputReadBySectionTool:
    __slots__ = ("_definition", "_store")

    def __init__(self, *, store: CachedToolOutputStore) -> None:
        self._store = store
        self._definition = ToolDefinition(
            llm_spec=ToolLLMSpec(
                name="read_cached_tool_output_by_section",
                description=(
                    "Read one or more sections from one cached tool output by "
                    "section_id.\n\n"
                    "Use exact section_id values returned by "
                    "inspect_cached_tool_output_structure. Each section returns "
                    "only its direct body; child sections remain separate entries. "
                    "Each item also includes the readable section_path for the "
                    "requested section_id. "
                    "budget_exhausted indicates omitted or truncated later windows."
                ),
                parameters_schema=ToolParametersSchema(_PARAMETERS_SCHEMA),
            ),
            policy=_policy(),
            ui_spec=ToolUISpec(
                display_name="按章节读取缓存的工具输出",
                description="根据目录中的章节 ID 读取指定章节直属正文。",
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
    ) -> CachedToolOutputReadBySectionResult:
        del config
        try:
            content_id = str(kwargs["content_id"])
            section_ids = [str(value).strip() for value in kwargs["section_ids"]]
            stored = await self._store.get(
                content_id=content_id,
                session_id=str(context["session_id"]),
            )
            if stored is None:
                return CachedToolOutputReadBySectionResult(
                    content_id=content_id,
                    items=[
                        CachedToolOutputReadBySectionItem(
                            section_id=section_id,
                            reason="cached_tool_output_not_found",
                        )
                        for section_id in dict.fromkeys(section_ids)
                    ],
                )
            return _read_by_section(
                content_id=content_id,
                section_ids=section_ids,
                sections=stored.sections,
                builder=CachedToolOutputWindowBuilder(
                    char_budget=settings.TOOL_CONTENT_READ_WINDOW_CHAR_BUDGET
                ),
                stored=stored,
            )
        except Exception as exc:
            raise ToolExecutionError(
                reason="read_cached_tool_output_by_section_failed",
                detail_reason=str(exc),
                retryable=False,
            ) from exc


def _read_by_section(
    *,
    content_id: str,
    section_ids: Sequence[str],
    sections: Sequence[Section],
    builder: CachedToolOutputWindowBuilder,
    stored: StoredCachedToolOutput,
) -> CachedToolOutputReadBySectionResult:
    sections_by_id = {section.section_id: section for section in sections}
    items: list[CachedToolOutputReadBySectionItem] = []
    remaining = settings.TOOL_CONTENT_READ_TOTAL_CHAR_BUDGET
    budget_exhausted = False

    for section_id in dict.fromkeys(section_ids):
        if remaining <= 0:
            budget_exhausted = True
            items.append(
                CachedToolOutputReadBySectionItem(
                    section_id=section_id,
                    reason="section_budget_exhausted",
                )
            )
            continue

        section = sections_by_id.get(section_id)
        if section is None:
            items.append(
                CachedToolOutputReadBySectionItem(
                    section_id=section_id,
                    reason="section_not_found",
                )
            )
            continue

        windows: list[CachedToolOutputWindow] = []
        reason = None
        for span in section.content_spans:
            if remaining <= 0:
                budget_exhausted = True
                reason = "section_budget_exhausted"
                break
            window = builder.build_range_window(
                stored,
                start=span.start_offset,
                end=span.end_offset,
                char_budget=remaining,
            )
            windows.append(window)
            remaining -= len(window.text)
            if window.truncated:
                budget_exhausted = True
                reason = "section_budget_exhausted"
                break
        items.append(
            CachedToolOutputReadBySectionItem(
                section_id=section_id,
                section_path=" > ".join(section.section_path),
                windows=windows,
                reason=reason,
            )
        )

    return CachedToolOutputReadBySectionResult(
        content_id=content_id,
        items=items,
        budget_exhausted=budget_exhausted,
    )


def _policy() -> ToolPolicy:
    return ToolPolicy(
        expose_by_default=False,
        selection_mode=ToolSelectionMode.CONTEXTUAL,
        persist_output=True,
        risk_level=ToolRiskLevel.LOW,
        required_context_keys=("session_id",),
        timeout_seconds=_TIMEOUT_SECONDS,
    )
