import json
from typing import Any, Set

from .models import ContentReceipt, ContentWindow

_METADATA_EXCLUDE_KEYS: Set[str] = {
    "content_kind",
    "page_chunk_map",
    "section_map",
    "anchors",
}


def _fmt_value(value: Any) -> str:
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def format_tool_content_window(window: ContentWindow) -> str:
    next_offset_str = "" if window.next_offset is None else str(window.next_offset)

    lines = [
        "[ToolContent Metadata]",
        f"content_id: {window.content_id}",
        f"content_cached: {str(window.cached).lower()}",
    ]

    if window.cache_error:
        lines.append(f"cache_error: {window.cache_error}")

    lines.extend([
        f"tool_name: {window.producer}",
        f"source: {window.source}",
        f"content_type: {window.content_type}",
        f"original_length: {window.original_length}",
        f"chunk_index: {window.chunk_index}",
        f"chunk_count: {window.chunk_count}",
        f"offset: {window.offset}",
        f"returned_length: {window.returned_length}",
        f"truncated: {str(window.truncated).lower()}",
        f"next_offset: {next_offset_str}",
    ])

    if window.error:
        lines.append(f"error: {window.error}")
    if window.warning:
        lines.append(f"warning: {window.warning}")
    if window.truncated:
        lines.append(
            "hint: content is truncated and split into many chunks. The first window may not "
            "contain the key evidence. Call evidence_rank with the user's question and this "
            "content_id to score all chunks by relevance and find the most relevant passages. "
            "If you need more context around a ranked passage, call tool_content_read with this "
            f"content_id and offset={next_offset_str} to read the next portion."
        )

    return "\n".join(lines) + f"\n\n[Content]\n{window.text}"


def format_tool_content_receipt(receipt: ContentReceipt) -> str:
    lines = [
        "[ToolContent Receipt]",
        f"content_id: {receipt.content_id}",
        f"content_cached: {str(receipt.cached).lower()}",
        f"tool_name: {receipt.producer}",
        f"source: {receipt.source}",
        f"content_type: {receipt.content_type}",
        f"original_length: {receipt.original_length}",
        f"chunk_count: {receipt.chunk_count}",
    ]

    if receipt.cache_error:
        lines.append(f"cache_error: {receipt.cache_error}")
    if receipt.error:
        lines.append(f"error: {receipt.error}")
    if receipt.warning:
        lines.append(f"warning: {receipt.warning}")

    if receipt.metadata:
        lines.extend(
            f"{key}: {_fmt_value(value)}"
            for key, value in receipt.metadata.items()
            if key not in _METADATA_EXCLUDE_KEYS
        )

    lines.extend([
        "",
        "[Content]",
        "omitted: true",
        "reason: Content is cached as a tool artifact; use content_id with the "
        "appropriate follow-up tool when deeper inspection is needed.",
    ])

    return "\n".join(lines)

