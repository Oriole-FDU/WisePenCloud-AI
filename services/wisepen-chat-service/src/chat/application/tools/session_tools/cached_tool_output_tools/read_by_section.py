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
from chat.application.tools.core.output_cache.cache_store import get_tool_content
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
class SectionContent:
    """单个 section 的模型可见内容；结构字段与续读窗口分开表达。"""

    section_id: str
    title: str
    section_path: str
    window: CachedToolOutputWindow


@dataclass(slots=True)
class CachedToolOutputReadBySectionResult:
    """按 section 读取结果；只返回实际成功定位到的 section。"""

    content_id: str
    section_contents: list[SectionContent] = field(default_factory=list)


class CachedToolOutputReadBySectionTool:

    def __init__(self) -> None:
        self._definition = ToolDefinition(
            llm_spec=ToolLLMSpec(
                name="read_cached_tool_output_by_section",
                description=(
                    "Read one or more sections from one cached tool output by "
                    "section_id.\n\n"
                    "Use exact section_id values returned by "
                    "inspect_cached_tool_output_structure. Each section returns "
                    "only its direct body; child sections remain separate entries. "
                    "Each entry in section_contents includes section_id, title, "
                    "section_path, and one window. Continue a truncated window "
                    "from its end_offset."
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
            content_id = kwargs["content_id"]
            section_ids = kwargs["section_ids"]
            stored = await get_tool_content(
                content_id=content_id,
                session_id=context["session_id"],
            )
            if stored is None:
                return CachedToolOutputReadBySectionResult(content_id=content_id)
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
    section_contents: list[SectionContent] = []
    remaining = settings.TOOL_CONTENT_READ_TOTAL_CHAR_BUDGET

    for section_id in dict.fromkeys(section_ids):
        if remaining <= 0:
            break
        section = sections_by_id.get(section_id)
        if section is None:
            continue

        window = builder.build_spans_window(
            stored,
            source_spans=section.content_spans,
            char_budget=remaining,
        )
        section_contents.append(
            SectionContent(
                section_id=section.section_id,
                title=section.title,
                section_path=" > ".join(section.section_path),
                window=window,
            )
        )
        remaining -= len(window.text)
        if window.truncated:
            break

    return CachedToolOutputReadBySectionResult(
        content_id=content_id,
        section_contents=section_contents,
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
