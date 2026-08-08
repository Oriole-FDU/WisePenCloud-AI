from __future__ import annotations

from rag.utils.chunkers import SourceSpan
from rag.application.rag.ingestion import RagSectionReadingBlock
from rag.application.rag.ingestion.revision import RagProjectionCheckpoint
from rag.domain.entities.rag_content import (
    RagContentPartDocument,
    RagProjectionCheckpointDocument,
    RagSectionReadingBlockDocument,
    RagSourceSpanDocument,
)

CONTENT_PART_CHARACTERS = 1_000_000


def to_spans(documents: list[RagSourceSpanDocument]) -> tuple[SourceSpan, ...]:
    return tuple(
        SourceSpan(
            start_offset=document.start_offset,
            end_offset=document.end_offset,
        )
        for document in documents
    )


def to_reading_block(
    document: RagSectionReadingBlockDocument,
) -> RagSectionReadingBlock:
    return RagSectionReadingBlock(
        block_id=document.block_id,
        section_id=document.section_id,
        ordinal=document.ordinal,
        raw_text=document.raw_text,
        source_spans=to_spans(document.source_spans),
        page_labels=tuple(document.page_labels),
        anchor_labels=tuple(document.anchor_labels),
    )


def read_source_spans(
    documents: list[RagContentPartDocument],
    spans: list[RagSourceSpanDocument],
) -> str:
    fragments: list[str] = []
    for span in spans:
        cursor = span.start_offset
        span_fragments: list[str] = []
        for document in documents:
            if document.end_offset - document.start_offset != len(document.text):
                raise RuntimeError(
                    f"content revision {document.content_revision} has an invalid part range"
                )
            if document.end_offset <= cursor:
                continue
            if document.start_offset >= span.end_offset:
                break
            if document.start_offset > cursor:
                raise RuntimeError("content parts do not cover source span")

            fragment_end = min(document.end_offset, span.end_offset)
            span_fragments.append(
                document.text[
                    cursor - document.start_offset : fragment_end
                    - document.start_offset
                ]
            )
            cursor = fragment_end
            if cursor == span.end_offset:
                break
        if cursor != span.end_offset:
            raise RuntimeError("content parts do not cover source span")
        fragments.append("".join(span_fragments))
    return "\n\n".join(fragments)


async def load_content_checkpoint(
    resource_id: str,
) -> RagProjectionCheckpoint | None:
    document = await RagProjectionCheckpointDocument.find_one(
        RagProjectionCheckpointDocument.resource_id == resource_id
    )
    if document is None:
        return None
    return RagProjectionCheckpoint(
        resource_id=document.resource_id,
        staged_content_revision=document.staged_content_revision,
        staged_document_version=document.staged_document_version,
        applied_content_revision=document.applied_content_revision,
        applied_document_version=document.applied_document_version,
    )


async def load_applied_content_revision(resource_id: str) -> str | None:
    checkpoint = await load_content_checkpoint(resource_id)
    if checkpoint is None:
        return None
    return checkpoint.applied_content_revision
