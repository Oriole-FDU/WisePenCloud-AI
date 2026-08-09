from __future__ import annotations

from collections.abc import Sequence
from hashlib import sha256

from beanie.operators import In
from rag.application.rag.evidence import RagMaterializedSource
from rag.application.rag.graph_extraction import (
    KnowledgeExtractionBlock,
    KnowledgeExtractionSource,
)
from rag.application.rag.ingestion import (
    RagSectionReadingBlock,
    RagSourceRef,
)
from rag.domain.entities.rag_content import (
    RagContentPartDocument,
    RagContentRevisionDocument,
    RagSectionDocument,
    RagSectionReadingBlockDocument,
    RagSourceRefDocument,
)
from rag.domain.repositories import (
    RagKnowledgeExtractionSourceRepository,
    RagSourceRepository,
)

from .common import (
    CONTENT_PART_CHARACTERS,
    read_source_spans,
    load_applied_content_revision,
    to_reading_block,
    to_spans,
)


class MongoRagExtractionSourceRepository(RagKnowledgeExtractionSourceRepository):
    """为知识图谱抽取读取当前 applied 正文和 SourceRef。"""

    async def load_applied_extraction_source(
        self,
        resource_id: str,
    ) -> KnowledgeExtractionSource | None:
        revision = await load_applied_content_revision(resource_id)
        if revision is None:
            return None

        content = await RagContentRevisionDocument.find_one(
            RagContentRevisionDocument.content_revision == revision
        )
        if content is None:
            raise RuntimeError(
                f"applied content revision {revision} is missing"
            )
        content_parts = (
            await RagContentPartDocument.find(
                RagContentPartDocument.content_revision == revision
            )
            .sort("part_index")
            .to_list()
        )
        source_ref_documents = (
            await RagSourceRefDocument.find(
                RagSourceRefDocument.content_revision == revision
            )
            .to_list()
        )
        reading_block_documents = (
            await RagSectionReadingBlockDocument.find(
                RagSectionReadingBlockDocument.content_revision == revision
            )
            .to_list()
        )
        section_documents = (
            await RagSectionDocument.find(
                RagSectionDocument.content_revision == revision
            )
            .to_list()
        )
        sections_by_id = {
            document.section_id: document for document in section_documents
        }
        source_ref_documents.sort(
            key=lambda document: (
                document.source_spans[0].start_offset,
                document.source_spans[-1].end_offset,
                document.ref_id,
            )
        )
        markdown = _join_content_parts(content_parts)
        if sha256(markdown.encode("utf-8")).hexdigest() != content.content_hash:
            raise RuntimeError(
                f"applied content revision {revision} has an invalid content hash"
            )
        source_refs = tuple(
            _to_source_ref(document)
            for document in source_ref_documents
        )
        reading_block_documents.sort(
            key=lambda document: (
                sections_by_id[document.section_id].own_start,
                document.ordinal,
                document.block_id,
            )
        )
        return KnowledgeExtractionSource(
            resource_id=content.resource_id,
            document_title=_document_title(tuple(section_documents)),
            document_version=content.document_version,
            content_revision=revision,
            markdown=markdown,
            blocks=tuple(
                KnowledgeExtractionBlock(
                    block_id=document.block_id,
                    block_index=block_index,
                    section_id=document.section_id,
                    section_path=tuple(
                        sections_by_id[document.section_id].section_path
                    ),
                    raw_text=document.raw_text,
                    source_spans=to_spans(document.source_spans),
                )
                for block_index, document in enumerate(reading_block_documents)
            ),
            source_refs=source_refs,
        )


class MongoRagSourceRepository(RagSourceRepository):
    """按 applied revision 回读 evidence 原文和 Section 阅读块。"""

    async def load_applied_reading_blocks(
        self,
        *,
        resource_id: str,
        reading_block_ids: Sequence[str],
    ) -> tuple[RagSectionReadingBlock, ...]:
        revision = await load_applied_content_revision(resource_id)
        if revision is None:
            return ()

        unique_ids = tuple(dict.fromkeys(reading_block_ids))
        if not unique_ids:
            return ()
        documents = await RagSectionReadingBlockDocument.find(
            RagSectionReadingBlockDocument.content_revision == revision,
            In(RagSectionReadingBlockDocument.block_id, unique_ids),
        ).to_list()
        by_id = {document.block_id: document for document in documents}
        return tuple(
            to_reading_block(by_id[block_id])
            for block_id in unique_ids
            if block_id in by_id
        )

    async def load_applied_sources(
        self,
        *,
        resource_id: str,
        ref_ids: Sequence[str],
    ) -> tuple[RagMaterializedSource, ...]:
        revision = await load_applied_content_revision(resource_id)
        if revision is None:
            return ()

        unique_ref_ids = tuple(dict.fromkeys(ref_ids))
        if not unique_ref_ids:
            return ()
        documents = await RagSourceRefDocument.find(
            RagSourceRefDocument.content_revision == revision,
            RagSourceRefDocument.resource_id == resource_id,
            In(RagSourceRefDocument.ref_id, unique_ref_ids),
        ).to_list()
        by_id = {document.ref_id: document for document in documents}
        ordered_documents = tuple(
            document
            for ref_id in unique_ref_ids
            if (document := by_id.get(ref_id)) is not None
        )
        part_indexes_to_load = sorted(
            {
                part_index
                for document in ordered_documents
                for span in document.source_spans
                for part_index in _part_indexes(
                    span.start_offset,
                    span.end_offset,
                )
            }
        )
        content_parts = (
            await RagContentPartDocument.find(
                RagContentPartDocument.content_revision == revision,
                In(RagContentPartDocument.part_index, part_indexes_to_load),
            )
            .sort("part_index")
            .to_list()
            if part_indexes_to_load
            else []
        )
        return tuple(
            RagMaterializedSource(
                source_ref=_to_source_ref(document),
                content=read_source_spans(content_parts, document.source_spans),
            )
            for document in ordered_documents
        )


def _document_title(section_documents: tuple[RagSectionDocument, ...]) -> str:
    top_level_sections = sorted(
        (
            document
            for document in section_documents
            if document.level == 1 and document.title.strip()
        ),
        key=lambda document: (document.own_start, document.ordinal),
    )
    if top_level_sections:
        return top_level_sections[0].title.strip()

    titled_sections = sorted(
        (document for document in section_documents if document.title.strip()),
        key=lambda document: (document.own_start, document.level, document.ordinal),
    )
    return titled_sections[0].title.strip() if titled_sections else ""


def _to_source_ref(document: RagSourceRefDocument) -> RagSourceRef:
    return RagSourceRef(
        ref_id=document.ref_id,
        resource_id=document.resource_id,
        document_version=document.document_version,
        chunk_id=document.chunk_id,
        section_id=document.section_id,
        section_path=tuple(document.section_path),
        source_spans=to_spans(document.source_spans),
        page_labels=tuple(document.page_labels),
        anchor_labels=tuple(document.anchor_labels),
    )


def _join_content_parts(documents: list[RagContentPartDocument]) -> str:
    expected_start = 0
    content: list[str] = []
    for document in documents:
        if document.start_offset != expected_start:
            raise RuntimeError(
                f"content revision {document.content_revision} has discontinuous parts"
            )
        if document.end_offset - document.start_offset != len(document.text):
            raise RuntimeError(
                f"content revision {document.content_revision} has an invalid part range"
            )
        content.append(document.text)
        expected_start = document.end_offset
    return "".join(content)


def _part_indexes(start_offset: int, end_offset: int) -> range:
    if start_offset < 0 or end_offset <= start_offset:
        raise RuntimeError("source span has an invalid range")
    return range(
        start_offset // CONTENT_PART_CHARACTERS,
        (end_offset - 1) // CONTENT_PART_CHARACTERS + 1,
    )
