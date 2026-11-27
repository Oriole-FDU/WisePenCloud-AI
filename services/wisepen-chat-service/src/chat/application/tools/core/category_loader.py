from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from typing import Any

from common.mcp import ToolCategoryMeta

from chat.application.tools.core.definition import (
    Tool,
    ToolDefinition,
    ToolLLMSpec,
    ToolParametersSchema,
    ToolPolicy,
    ToolRiskLevel,
    ToolUISpec,
)


_MARKDOWN_PREFIX = re.compile(r"^(?:#{1,6}\s+|[-*+]\s+|\d+[.)]\s+)")
_SENTENCE_END = re.compile(r"(?<=[.!?。！？])\s+")


def _compact_tool_description(description: str, *, max_chars: int = 180) -> str:
    """从完整工具说明中提取稳定、紧凑的一行用途说明。"""
    semantic_lines: list[str] = []
    for raw_line in (description or "").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        line = _MARKDOWN_PREFIX.sub("", line).strip()
        if line:
            semantic_lines.append(line)
    normalized = " ".join(semantic_lines)
    if not normalized:
        return "No description provided."
    first_sentence = _SENTENCE_END.split(normalized, maxsplit=1)[0].strip()
    if len(first_sentence) <= max_chars:
        return first_sentence
    return first_sentence[: max_chars - 1].rstrip() + "…"


def _render_category_loader_description(
    category: ToolCategoryMeta,
    tools: Sequence[Tool],
) -> str:
    lines = [
        f"Load {category.display_name} tools for the current turn.",
        "",
        category.description.strip(),
        "",
        "Available tools:",
    ]
    for tool in sorted(tools, key=lambda item: item.definition.llm_spec.name):
        spec = tool.definition.llm_spec
        lines.append(f"- `{spec.name}`: {_compact_tool_description(spec.description)}")
    lines.extend([
        "",
        "Calling this loader only makes these tools available for the current turn; "
        "it does not execute any business operation.",
    ])
    return "\n".join(lines)


def _persistence_placeholder(arguments: dict[str, Any], output: Any) -> str:
    return "[Tool category was loaded for that turn.]"


class CategoryLoaderTool:
    def __init__(
        self,
        *,
        category: ToolCategoryMeta,
        tools: Sequence[Tool],
        load_category: Callable[[str], dict[str, Any]],
    ) -> None:
        self.category_name = category.name
        self._load_category = load_category
        self._definition = ToolDefinition(
            llm_spec=ToolLLMSpec(
                name=f"load_{category.name}_tools",
                description=_render_category_loader_description(category, tools),
                parameters_schema=ToolParametersSchema({"type": "object", "properties": {}}),
            ),
            policy=ToolPolicy(
                category="resident",
                expose_by_default=True,
                risk_level=ToolRiskLevel.LOW,
                persist_output=False,
                persisted_output_placeholder_factory=_persistence_placeholder,
                timeout_seconds=5.0,
                allow_parallel=True,
            ),
            ui_spec=ToolUISpec(
                display_name=f"加载{category.display_name}工具",
                description=f"为当前轮次加载{category.display_name}工具。",
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
    ) -> dict[str, Any]:
        return self._load_category(self.category_name)


__all__ = ["CategoryLoaderTool"]
