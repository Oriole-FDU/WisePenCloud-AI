from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

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
    ToolContentStore as CachedToolOutputStore,
)
from common.utils.chunkers import LocatorKind, TextLocator
from chat.core.config.app_settings import settings

from chat.application.tools.session_tools.cached_tool_output_tools.window import CachedToolOutputWindow, CachedToolOutputWindowBuilder

_TIMEOUT_SECONDS = 300.0
_PARAMETERS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "content_id": {
            "type": "string",
            "minLength": 1,
            "description": "Required. One cached tool output content_id returned in a previous tool result.",
        },
        "section_paths": {
            "type": "array",
            "items": {"type": "string", "minLength": 1},
            "minItems": 1,
            "maxItems": 20,
            "description": (
                "Exact section_path strings returned by inspect_cached_tool_output_structure, "
                "for example \"Methods > Dataset\"."
            ),
        },
    },
    "required": ["content_id", "section_paths"],
    "additionalProperties": False,
}


@dataclass(slots=True)
class CachedToolOutputReadBySectionItem:
    section_path: str
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
                    "Read one or more sections from one cached tool output content_id by section_path values.\n\n"
                    "WHEN TO TRIGGER:\n"
                    "  - MUST trigger when inspect_cached_tool_output_structure identifies specific sections.\n"
                    "  - SHOULD pass multiple sibling or related section_paths in one call.\n"
                    "DO NOT TRIGGER when:\n"
                    "  - You need physical pages; use read_cached_tool_output_by_page.\n"
                    "  - You only know a semantic question or exact phrase; search first.\n\n"
                    "INPUT RULES:\n"
                    "  - section_paths are exact strings from structure, joined with \" > \".\n"
                    "OUTPUT RULES:\n"
                    "  - items[].section_path echoes the requested section_path.\n"
                    "  - budget_exhausted indicates that later section windows were omitted."
                ),
                parameters_schema=ToolParametersSchema(_PARAMETERS_SCHEMA),
            ),
            policy=_policy(),
            ui_spec=ToolUISpec(
                display_name="按章节读取缓存的工具输出",
                description="根据缓存工具输出结构中的章节路径读取指定章节正文。",
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
            # section_paths 是模型从 structure 结果里取回的字符串，保持 list 语义即可。
            section_paths = [str(value).strip() for value in kwargs["section_paths"]]
            # store 读取时携带 session_id，防止跨会话读取缓存正文。
            stored = await self._store.get(
                content_id=content_id,
                session_id=str(context["session_id"]),
            )
            if stored is None:
                # content 不存在时逐项返回 cached_tool_output_not_found，便于模型保留原请求上下文。
                unique_paths = list(dict.fromkeys(section_paths))
                return CachedToolOutputReadBySectionResult(
                    content_id=content_id,
                    items=[
                        CachedToolOutputReadBySectionItem(
                            section_path=section_path,
                            reason="cached_tool_output_not_found",
                        )
                        for section_path in unique_paths
                    ],
                )
            return _read_by_section(
                content_id=content_id,
                section_paths=section_paths,
                locators=stored.locators,
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
    section_paths: Sequence[str],
    locators: Sequence[TextLocator],
    builder: CachedToolOutputWindowBuilder,
    stored: StoredCachedToolOutput,
) -> CachedToolOutputReadBySectionResult:
    # section_path 去重保序，避免重复消耗窗口预算。
    unique_section_paths = list(dict.fromkeys(section_paths))
    # section locator 保存的是结构路径对应的原文范围。
    sections_by_path = _section_locators_by_path(locators)
    items: list[CachedToolOutputReadBySectionItem] = []
    remaining = settings.TOOL_CONTENT_READ_TOTAL_CHAR_BUDGET
    budget_exhausted = False

    for section_path in unique_section_paths:
        if remaining <= 0:
            # 预算耗尽是读取能力限制，不等同于 section 不存在。
            budget_exhausted = True
            items.append(
                CachedToolOutputReadBySectionItem(
                    section_path=section_path,
                    reason="section_budget_exhausted",
                )
            )
            continue

        section_ranges = sections_by_path.get(section_path, [])
        if not section_ranges:
            # section_path 必须精确匹配 structure 返回值，否则明确返回 section_not_found。
            items.append(
                CachedToolOutputReadBySectionItem(
                    section_path=section_path,
                    reason="section_not_found",
                )
            )
            continue

        windows = []
        reason = None
        for section_range in section_ranges:
            if remaining <= 0:
                # 一个 section 可能跨多个 locator 片段，所有片段共享本次读取总预算。
                budget_exhausted = True
                reason = "section_budget_exhausted"
                break
            window = builder.build_range_window(
                stored,
                start=section_range.start_offset,
                end=section_range.end_offset,
                char_budget=remaining,
            )
            windows.append(window)
            remaining -= len(window.text)
            if window.truncated:
                # 截断后由调用方按返回的 offset 继续读取，避免一次返回过长正文。
                budget_exhausted = True
                reason = "section_budget_exhausted"
                break
        items.append(
            CachedToolOutputReadBySectionItem(
                section_path=section_path,
                windows=windows,
                reason=reason,
            )
        )

    return CachedToolOutputReadBySectionResult(
        content_id=content_id,
        items=items,
        budget_exhausted=budget_exhausted,
    )


def _section_locators_by_path(
    locators: Sequence[TextLocator],
) -> dict[str, list[TextLocator]]:
    # section locator 的 name 带 section: 前缀，对外只暴露可读的 section_path。
    indexed: dict[str, list[TextLocator]] = {}
    for locator in locators:
        if locator.kind is LocatorKind.SECTION:
            indexed.setdefault(locator.name.removeprefix("section:"), []).append(
                locator
            )
    return indexed


def _policy() -> ToolPolicy:
    return ToolPolicy(
        expose_by_default=False,
        selection_mode=ToolSelectionMode.CONTEXTUAL,
        persist_output=True,
        risk_level=ToolRiskLevel.LOW,
        required_context_keys=("session_id",),
        timeout_seconds=_TIMEOUT_SECONDS,
    )
