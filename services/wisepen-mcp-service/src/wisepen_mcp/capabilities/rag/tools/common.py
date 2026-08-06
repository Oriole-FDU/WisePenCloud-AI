from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Any

from common.core.exceptions import ServiceException
from mcp.server.fastmcp import Context
from pydantic import Field, StringConstraints

from wisepen_mcp.capabilities.core.tools import CacheableText, get_tool_context_value
from wisepen_mcp.domain.error_codes import McpErrorCode

_SOURCE_PREVIEW_CHARS = 600

StateId = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1),
    Field(description="Reuse the exact state_id returned by locate or an earlier navigation call."),
]


def session_id(ctx: Context) -> str:
    value = get_tool_context_value(ctx, "session_id")
    if not isinstance(value, str) or not value.strip():
        raise ServiceException(
            McpErrorCode.RAG_NAVIGATION_INVALID,
            "session_id is missing from MCP tool context.",
        )
    return value.strip()


@dataclass(frozen=True, slots=True)
class RagTextRenderBudget:
    """RAG 正文在 visible result 与 cacheable_texts 之间分流时使用的字符预算。"""

    window_char_budget: int
    total_char_budget: int

    def __post_init__(self) -> None:
        if self.window_char_budget < 1 or self.total_char_budget < 1:
            raise ValueError("RAG text render budgets must be greater than 0")


class RagTextRenderRouter:
    """把 RAG 正文分流为直接可见文本或后续可续读的 cacheable_text。

    RAG 自己已经有 page、section、state 等结构化定位锚点，因此小窗口正文
    直接返回给模型即可；只有当单窗口或本次总可见正文超过安全预算时，
    才进入 ToolReturn.cacheable_texts，交给 chat-service 的 ToolContentStore
    提供 range 续读能力。
    """

    __slots__ = ("_budget", "_cacheable_texts", "_remaining_visible_chars")

    def __init__(self, budget: RagTextRenderBudget) -> None:
        self._budget = budget
        self._cacheable_texts: list[CacheableText] = []
        self._remaining_visible_chars = budget.total_char_budget

    @property
    def cacheable_texts(self) -> list[CacheableText]:
        return self._cacheable_texts

    def text_payload(self, text: str, *, metadata: dict[str, Any]) -> dict[str, Any]:
        if (
            len(text) <= self._budget.window_char_budget
            and len(text) <= self._remaining_visible_chars
        ):
            self._remaining_visible_chars -= len(text)
            return {"text": text}

        # 超出安全可见窗口时才生成 content_index。这个 index 只用于
        # cacheable_texts 和后续 range 续读，不再伪装成 RAG 的结构锚点。
        content_index = len(self._cacheable_texts)
        self._cacheable_texts.append(
            {
                "text": text,
                "is_md": True,
                "metadata": metadata,
            }
        )
        return {
            "content_index": content_index,
            "preview": preview(text),
        }


def section_view_payload(
    view: dict[str, Any],
    text_router: RagTextRenderRouter,
    source_content_indices: dict[str, int] | None = None,
) -> dict[str, Any]:
    section_path = view["section_path"]
    reading_blocks = []
    for block in view["reading_blocks"]:
        text_payload = text_router.text_payload(
            block["raw_text"],
            metadata={
                "resource_id": view["resource_id"],
                "section_id": view["section_id"],
                "reading_block_id": block["block_id"],
                "section_path": section_path,
                "page_labels": block["page_labels"],
                "anchor_labels": block["anchor_labels"],
            },
        )
        reading_blocks.append(
            {
                "reading_block_id": block["block_id"],
                **text_payload,
                "page_labels": block["page_labels"],
                "anchor_labels": block["anchor_labels"],
            }
        )

    evidence = []
    for source in view["evidence"]:
        text_payload = text_router.text_payload(
            source["content"],
            metadata={
                "resource_id": source["resource_id"],
                "section_id": source["section_id"],
                "source_ref_id": source["ref_id"],
                "section_path": source["section_path"],
                "page_labels": source["page_labels"],
                "anchor_labels": source["anchor_labels"],
            },
        )
        if source_content_indices is not None and "content_index" in text_payload:
            source_content_indices[source["ref_id"]] = text_payload["content_index"]
        evidence.append(
            {
                "source_ref_id": source["ref_id"],
                **text_payload,
                "page_labels": source["page_labels"],
                "anchor_labels": source["anchor_labels"],
            }
        )

    return {
        "resource_id": view["resource_id"],
        "section_id": view["section_id"],
        "title": view["title"],
        "section_path": section_path,
        "preview": view["preview"],
        "has_content": view["has_content"],
        "reading_blocks": reading_blocks,
        "evidence": evidence,
        "frontier": _frontier_payload(view["frontier"]),
    }


def preview(text: str) -> str:
    value = text.strip()
    if len(value) <= _SOURCE_PREVIEW_CHARS:
        return value
    return f"{value[:_SOURCE_PREVIEW_CHARS].rstrip()}..."


def _frontier_payload(frontier: dict[str, Any]) -> dict[str, Any]:
    return {
        "parent": _section_choice_payload(frontier["parent"]),
        "previous": _section_choice_payload(frontier["previous"]),
        "next": _section_choice_payload(frontier["next"]),
        "children": [
            _section_choice_payload(child) for child in frontier["children"]
        ],
    }


def _section_choice_payload(section: dict[str, Any] | None) -> dict[str, Any] | None:
    if section is None:
        return None
    return {
        "section_id": section["section_id"],
        "title": section["title"],
        "section_path": section["section_path"],
        "preview": section["preview"],
        "has_content": section["has_content"],
    }
