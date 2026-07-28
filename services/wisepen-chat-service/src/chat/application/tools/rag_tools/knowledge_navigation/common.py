from __future__ import annotations

from typing import Any

from chat.application.rag.evidence import (
    RagEvidenceUnavailableError,
    RagMaterializedSource,
)
from chat.application.rag.ingestion import RagSectionReadingBlock
from chat.application.rag.section_navigation import RagSectionView
from chat.application.tools.core import ToolExecutionError
from chat.application.tools.core.output.tool_return import CacheableText

_SOURCE_PREVIEW_CHARS = 600


def navigation_backend_error(error: Exception) -> ToolExecutionError:
    return ToolExecutionError(
        reason="knowledge_navigation_backend_unavailable",
        detail_reason=(
            str(error)
            if isinstance(error, RagEvidenceUnavailableError)
            else type(error).__name__
        ),
        retryable=True,
    )


def section_view_payload(
    view: RagSectionView,
    cacheable_texts: list[CacheableText],
) -> dict[str, Any]:
    section = view.section
    return {
        "resource_id": section.resource_id,
        "document_version": section.document_version,
        **section.to_tree_payload(),
        "reading_blocks": [
            _reading_block_payload(block, cacheable_texts)
            for block in view.reading_blocks
        ],
        "evidence": [
            _source_payload(source, cacheable_texts) for source in view.sources
        ],
        "frontier": {
            "parent": view.parent.to_tree_payload() if view.parent is not None else None,
            "previous": (
                view.previous.to_tree_payload() if view.previous is not None else None
            ),
            "next": view.next.to_tree_payload() if view.next is not None else None,
            "children": [child.to_tree_payload() for child in view.children],
        },
    }


def _reading_block_payload(
    block: RagSectionReadingBlock,
    cacheable_texts: list[CacheableText],
) -> dict[str, Any]:
    return {
        "reading_block_id": block.block_id,
        "ordinal": block.ordinal,
        "content_index": _append_cacheable_text(cacheable_texts, block.raw_text),
        "content_start": min(span.start_offset for span in block.source_spans),
        "content_end": max(span.end_offset for span in block.source_spans),
        "page_labels": list(block.page_labels),
        "anchor_labels": list(block.anchor_labels),
        "preview": _preview(block.raw_text),
    }


def _source_payload(
    source: RagMaterializedSource,
    cacheable_texts: list[CacheableText],
) -> dict[str, Any]:
    source_ref = source.source_ref
    return {
        "ref_id": source_ref.ref_id,
        "content_index": _append_cacheable_text(cacheable_texts, source.content),
        "content_start": min(span.start_offset for span in source_ref.source_spans),
        "content_end": max(span.end_offset for span in source_ref.source_spans),
        "page_label": source_ref.page_label,
        "anchor_labels": list(source_ref.anchor_labels),
        "preview": _preview(source.content),
    }


def _append_cacheable_text(
    cacheable_texts: list[CacheableText],
    text: str,
) -> int:
    content_index = len(cacheable_texts)
    cacheable_texts.append(CacheableText(text=text, is_md=True))
    return content_index


def _preview(text: str) -> str:
    preview = text.strip()
    if len(preview) <= _SOURCE_PREVIEW_CHARS:
        return preview
    return f"{preview[:_SOURCE_PREVIEW_CHARS].rstrip()}..."
