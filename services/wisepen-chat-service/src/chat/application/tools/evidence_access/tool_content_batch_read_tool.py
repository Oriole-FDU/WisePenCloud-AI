from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set, Tuple

from chat.application.infra.content_store.models import ContentWindow, StoredContent
from chat.application.tools.tool_content_store import ToolContentStore
from chat.domain.interfaces.tool import BaseTool

TOOL_DESCRIPTION = (
    "Reads multiple cached tool-content chunk windows in one call. "
    "Use this after evidence_rank when multiple ranked chunk evidence items are thematically related "
    "but located in different chunks.\n\n"
    "Always pass all selected ranked chunks in one items array.\n"
    "Each item MUST provide content_id and chunk_index.\n"
    "Use before_chunks and after_chunks only to expand context around the target chunk.\n"
    "Do not use this tool for sequential scanning, blind exploration, or as a replacement for evidence_rank."
)

TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "minItems": 1,
            "maxItems": 8,
            "items": {
                "type": "object",
                "properties": {
                    "content_id": {
                        "type": "string",
                        "minLength": 1,
                        "description": "cnt_* ToolContent identifier. Do not pass file_ref values.",
                    },
                    "chunk_index": {
                        "type": "integer",
                        "minimum": 0,
                        "description": "Chunk index returned by evidence_rank.",
                    },
                    "before_chunks": {"type": "integer", "minimum": 0, "maximum": 3, "default": 1},
                    "after_chunks":  {"type": "integer", "minimum": 0, "maximum": 3, "default": 1},
                },
                "required": ["content_id", "chunk_index"],
                "additionalProperties": False,
            },
        },
        "max_total_chars": {
            "type": "integer",
            "minimum": 1000,
            "maximum": 30000,
            "default": 12000,
            "description": "Maximum total output size. Output stops at window boundary.",
        },
    },
    "required": ["items"],
    "additionalProperties": False,
}


@dataclass(frozen=True, slots=True)
class BatchReadItem:
    content_id: str
    chunk_index: int
    before_chunks: int
    after_chunks: int


@dataclass(frozen=True, slots=True)
class BatchReadResultItem:
    index: int
    content_id: str
    chunk_index: int
    status: str
    block: str


@dataclass(frozen=True, slots=True)
class TargetChunkStructure:
    heading_path: Tuple[str, ...] = ()
    page_number: Optional[int] = None
    section_type: str = ""


class ToolContentBatchReadTool(BaseTool):
    def __init__(self, *, content_store: ToolContentStore) -> None:
        """初始化批量 ToolContent 读取工具。"""
        self._content_store = content_store

    @property
    def name(self) -> str:
        return "tool_content_batch_read"

    @property
    def description(self) -> str:
        return TOOL_DESCRIPTION

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return TOOL_SCHEMA

    async def execute(self, context: Dict[str, Any], **kwargs) -> str:
        session_id: Optional[str] = context.get("session_id")
        if not session_id:
            return "[Tool Error] Missing session_id in execution context."

        validation_error, validated_items = _validate_items(kwargs.get("items"))
        if validation_error:
            return validation_error

        max_total_chars = kwargs.get("max_total_chars", 12000)
        if not isinstance(max_total_chars, int) or not (1000 <= max_total_chars <= 30000):
            return "[Tool Error] max_total_chars must be an integer between 1000 and 30000."

        blocks: List[str] = []
        result_items: List[BatchReadResultItem] = []
        total_chars = 0
        per_item_budget = max(1, max_total_chars // len(validated_items))

        for index, item in enumerate(validated_items, 1):
            requested_content_id = item.content_id
            content_id, redirect_note = self._content_store.canonicalize_content_id(
                content_id=requested_content_id,
                session_id=session_id,
            )
            item = BatchReadItem(
                content_id=content_id,
                chunk_index=item.chunk_index,
                before_chunks=item.before_chunks,
                after_chunks=item.after_chunks,
            )
            stored = self._content_store.get(
                content_id=item.content_id,
                session_id=session_id,
            )
            window = (
                self._content_store.read_chunk_window_by_index(
                    content_id=item.content_id,
                    session_id=session_id,
                    chunk_index=item.chunk_index,
                    before_chunks=item.before_chunks,
                    after_chunks=item.after_chunks,
                )
                if stored is not None else None
            )

            if window is None:
                block = _error_block(index, item)
            else:
                block = _build_window_block(
                    index,
                    item,
                    window,
                    _extract_structure(stored, item.chunk_index),
                    requested_content_id=requested_content_id,
                    redirect_note=redirect_note,
                )

            status = "ok"
            if len(block) > per_item_budget:
                block = _truncate_block(
                    block=block,
                    budget=per_item_budget,
                    item=item,
                    reason="per_item_budget_exceeded",
                )
                status = "partial"

            remaining_budget = max_total_chars - total_chars
            if len(block) > remaining_budget:
                block = _truncate_block(
                    block=block,
                    budget=remaining_budget,
                    item=item,
                    reason="max_total_chars_exceeded",
                )
                status = "partial"

            blocks.append(block)
            total_chars += len(block)
            result_items.append(BatchReadResultItem(
                index=index,
                content_id=item.content_id,
                chunk_index=item.chunk_index,
                status=status,
                block=block,
            ))

        lines = [
            "[Tool Result] Batch Tool Content Windows",
            f"Requested: {len(validated_items)} item(s)",
            f"Returned: {len(blocks)} window(s)",
            "Skipped: 0 item(s)",
        ]
        partial_count = sum(1 for item in result_items if item.status == "partial")
        if partial_count:
            lines.append(f"Partial: {partial_count} item(s)")
        lines.append("")
        lines.extend(blocks)

        return "\n".join(lines).rstrip()


def _error_block(index: int, item: BatchReadItem) -> str:
    return (
        f"[{index}]\n"
        f"content_id: {item.content_id}\n"
        f"chunk_index: {item.chunk_index}\n"
        "error: cached tool content not found, expired, or inaccessible."
    )


def _extract_structure(stored: StoredContent, chunk_index: int) -> TargetChunkStructure:
    if not (0 <= chunk_index < len(stored.chunks)):
        return TargetChunkStructure()
    meta = stored.chunks[chunk_index].metadata
    if not isinstance(meta, dict):
        return TargetChunkStructure()

    h_path = meta.get("heading_path")
    p_num = meta.get("page_number")
    s_type = meta.get("section_type")

    return TargetChunkStructure(
        heading_path=tuple(h for h in h_path if isinstance(h, str) and h) if isinstance(h_path, (list, tuple)) else (),
        page_number=p_num if isinstance(p_num, int) else None,
        section_type=s_type if (isinstance(s_type, str) and s_type and s_type.strip() == s_type) else "",
    )


def _build_window_block(
    index: int,
    item: BatchReadItem,
    window: ContentWindow,
    structure: TargetChunkStructure,
    *,
    requested_content_id: str,
    redirect_note: Optional[str],
) -> str:
    lines = [
        f"[{index}]",
        "status: ok",
        f"content_id: {item.content_id}",
        f"chunk_index: {item.chunk_index}",
        f"before_chunks: {item.before_chunks}",
        f"after_chunks: {item.after_chunks}",
    ]
    if requested_content_id != item.content_id:
        lines.append(f"requested_content_id: {requested_content_id}")
    if redirect_note:
        lines.append(f"redirect_note: {redirect_note}")
    if structure.heading_path:
        lines.append("target_heading_path: " + " > ".join(structure.heading_path))
    if structure.page_number is not None:
        lines.append(f"target_page_number: {structure.page_number}")
    if structure.section_type:
        lines.append(f"target_section_type: {structure.section_type}")
    lines.extend([
        f"returned_length: {window.returned_length}",
        f"truncated: {str(window.truncated).lower()}",
    ])
    if window.error:
        lines.append(f"error: {window.error}")
    lines.extend(["", "[Content]", window.text])
    return "\n".join(lines)


def _truncate_block(
    *,
    block: str,
    budget: int,
    item: BatchReadItem,
    reason: str,
) -> str:
    suffix = (
        "\ntruncated: true"
        f"\nreason: {reason}"
        "\nsuggested_next_call: "
        f'{{"content_id": "{item.content_id}", "chunk_index": {item.chunk_index}, '
        '"before_chunks": 0, "after_chunks": 0, "max_total_chars": 4000}}'
    )
    available = max(0, budget - len(suffix))
    return block[:available].rstrip() + suffix


def _validate_items(raw_items: Any) -> Tuple[Optional[str], List[BatchReadItem]]:
    if not isinstance(raw_items, list):
        return "[Tool Error] items must be a list.", []
    if not raw_items:
        return "[Tool Error] items must contain at least one item.", []
    if len(raw_items) > 8:
        return "[Tool Error] items must contain at most 8 items.", []

    seen: Set[Tuple[str, int]] = set()
    validated: List[BatchReadItem] = []

    for index, raw_item in enumerate(raw_items, 1):
        if not isinstance(raw_item, dict):
            return f"[Tool Error] items[{index}] must be an object.", []

        content_id = raw_item.get("content_id")
        if not isinstance(content_id, str) or not content_id:
            return f"[Tool Error] items[{index}].content_id must be a non-empty string.", []
        if content_id.strip() != content_id:
            return f"[Tool Error] items[{index}].content_id must not contain leading or trailing whitespace.", []
        if content_id.startswith("file_ref"):
            return f"[Tool Error] items[{index}].content_id must be a cnt_* value, not a file_ref value.", []

        chunk_index = raw_item.get("chunk_index")
        if not isinstance(chunk_index, int) or chunk_index < 0:
            return f"[Tool Error] items[{index}].chunk_index must be an integer greater than or equal to 0.", []

        before_chunks = raw_item.get("before_chunks", 1)
        after_chunks = raw_item.get("after_chunks", 1)

        if not isinstance(before_chunks, int) or not (0 <= before_chunks <= 3):
            return f"[Tool Error] items[{index}].before_chunks must be between 0 and 3.", []
        if not isinstance(after_chunks, int) or not (0 <= after_chunks <= 3):
            return f"[Tool Error] items[{index}].after_chunks must be between 0 and 3.", []

        key = (content_id, chunk_index)
        if key in seen:
            return "[Tool Error] duplicate content_id + chunk_index items are not allowed.", []
        seen.add(key)

        validated.append(BatchReadItem(
            content_id=content_id,
            chunk_index=chunk_index,
            before_chunks=before_chunks,
            after_chunks=after_chunks,
        ))

    return None, validated
