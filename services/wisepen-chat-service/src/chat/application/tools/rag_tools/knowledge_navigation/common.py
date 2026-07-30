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
        **section.to_tree_payload(),
        "reading_blocks": [
            _reading_block_payload(
                block,
                resource_id=section.resource_id,
                section_path=section.section_path,
                cacheable_texts=cacheable_texts,
            )
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
    *,
    resource_id: str,
    section_path: tuple[str, ...],
    cacheable_texts: list[CacheableText],
) -> dict[str, Any]:
    return {
        "content_index": _append_cacheable_text(
            cacheable_texts,
            block.raw_text,
            metadata={
                "kind": "rag_section_reading_block",
                "resource_id": resource_id,
                "section_id": block.section_id,
                "section_path": section_path,
                "reading_block_id": block.block_id,
                "page_labels": block.page_labels,
                "anchor_labels": block.anchor_labels,
            },
        ),
        "preview": _preview(block.raw_text),
    }


def _source_payload(
    source: RagMaterializedSource,
    cacheable_texts: list[CacheableText],
) -> dict[str, Any]:
    source_ref = source.source_ref
    return {
        "content_index": _append_cacheable_text(
            cacheable_texts,
            source.content,
            metadata={
                "kind": "rag_evidence",
                "resource_id": source_ref.resource_id,
                "section_id": source_ref.section_id,
                "section_path": source_ref.section_path,
                "source_ref_id": source_ref.ref_id,
                "chunk_id": source_ref.chunk_id,
                "page_label": source_ref.page_label,
                "anchor_labels": source_ref.anchor_labels,
            },
        ),
        "preview": _preview(source.content),
    }


def _append_cacheable_text(
    cacheable_texts: list[CacheableText],
    text: str,
    *,
    metadata: dict[str, object],
) -> int:
    content_index = len(cacheable_texts)
    cacheable_texts.append(CacheableText(text=text, is_md=True, metadata=metadata))
    return content_index


def _preview(text: str) -> str:
    preview = text.strip()
    if len(preview) <= _SOURCE_PREVIEW_CHARS:
        return preview
    return f"{preview[:_SOURCE_PREVIEW_CHARS].rstrip()}..."
