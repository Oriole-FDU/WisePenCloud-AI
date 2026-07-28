from __future__ import annotations

from collections.abc import Sequence
from hashlib import sha256

from beanie.operators import In
from chat.application.rag.evidence import RagMaterializedSource
from chat.application.rag.graph_extraction import (
    KnowledgeExtractionChunk,
    KnowledgeExtractionSource,
)
from chat.application.rag.ingestion import (
    RagContentProjection,
    RagProjectionCheckpoint,
    RagProjectionStage,
    RagProjectionStageAction,
    RagSectionNode,
    RagSectionReadingBlock,
    RagSourceRef,
    prepare_projection_stage,
)
from chat.application.rag.section_navigation import RagSectionView
from chat.application.utils.chunkers import SourceSpan
from chat.domain.entities.rag_content import (
    RagContentPartDocument,
    RagContentRevisionDocument,
    RagProjectionCheckpointDocument,
    RagSectionDocument,
    RagSectionReadingBlockDocument,
    RagSourceRefDocument,
    RagSourceSpanDocument,
)

_CONTENT_PART_CHARACTERS = 1_000_000


class RagProjectionCommitError(RuntimeError):
    """staged revision 已变化，当前内容投影不能切换为 applied。"""


class MongoRagContentProjectionRepository:
    async def delete_resources(self, resource_ids: tuple[str, ...]) -> None:
        unique_resource_ids = tuple(dict.fromkeys(resource_ids))
        if not unique_resource_ids:
            return

        revisions = await RagContentRevisionDocument.find(
            In(RagContentRevisionDocument.resource_id, unique_resource_ids)
        ).to_list()
        content_revisions = tuple(document.content_revision for document in revisions)

        # checkpoint 先删除，后续清理失败时查询链路也会立即 fail closed。
        await RagProjectionCheckpointDocument.find(
            In(RagProjectionCheckpointDocument.resource_id, unique_resource_ids)
        ).delete()
        if not content_revisions:
            return
        for document_type in (
            RagContentPartDocument,
            RagSectionDocument,
            RagSectionReadingBlockDocument,
            RagSourceRefDocument,
            RagContentRevisionDocument,
        ):
            await document_type.find(
                In(document_type.content_revision, content_revisions)
            ).delete()

    async def stage_projection(
        self,
        projection: RagContentProjection,
    ) -> RagProjectionStage:
        checkpoint = await self.get_checkpoint(projection.resource_id)
        stage = prepare_projection_stage(projection, checkpoint)
        if stage.action is not RagProjectionStageAction.STAGED:
            return stage

        await self._replace_revision(stage.content_revision, projection)
        await RagProjectionCheckpointDocument.get_pymongo_collection().update_one(
            {"resource_id": projection.resource_id},
            {
                "$set": {
                    "staged_content_revision": stage.content_revision,
                    "staged_document_version": stage.document_version,
                },
                "$setOnInsert": {
                    "resource_id": projection.resource_id,
                },
            },
            upsert=True,
        )
        return stage

    async def apply_projection(self, stage: RagProjectionStage) -> None:
        if stage.action is not RagProjectionStageAction.STAGED:
            return

        result = (
            await RagProjectionCheckpointDocument.get_pymongo_collection().update_one(
                {
                    "resource_id": stage.resource_id,
                    "staged_content_revision": stage.content_revision,
                    "staged_document_version": stage.document_version,
                },
                {
                    "$set": {
                        "applied_content_revision": stage.content_revision,
                        "applied_document_version": stage.document_version,
                        "staged_content_revision": None,
                        "staged_document_version": None,
                    }
                },
            )
        )
        if result.modified_count == 1:
            return

        checkpoint = await self.get_checkpoint(stage.resource_id)
        if (
            checkpoint is not None
            and checkpoint.applied_content_revision == stage.content_revision
        ):
            return
        raise RagProjectionCommitError(
            f"content revision {stage.content_revision} is no longer staged"
        )

    async def get_checkpoint(
        self,
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

    async def get_applied_revisions(
        self,
        resource_ids: Sequence[str],
    ) -> dict[str, str]:
        unique_resource_ids = tuple(dict.fromkeys(resource_ids))
        if not unique_resource_ids:
            return {}
        documents = await RagProjectionCheckpointDocument.find(
            In(RagProjectionCheckpointDocument.resource_id, unique_resource_ids)
        ).to_list()
        return {
            document.resource_id: document.applied_content_revision
            for document in documents
            if document.applied_content_revision is not None
        }

    async def load_applied_extraction_source(
        self,
        resource_id: str,
    ) -> KnowledgeExtractionSource | None:
        checkpoint = await self.get_checkpoint(resource_id)
        if checkpoint is None or checkpoint.applied_content_revision is None:
            return None

        revision = checkpoint.applied_content_revision
        content = await RagContentRevisionDocument.find_one(
            RagContentRevisionDocument.content_revision == revision
        )
        if content is None:
            raise RagProjectionCommitError(
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
        source_ref_documents.sort(
            key=lambda document: (
                document.source_spans[0].start_offset,
                document.source_spans[-1].end_offset,
                document.ref_id,
            )
        )
        markdown = _join_content_parts(content_parts)
        if sha256(markdown.encode("utf-8")).hexdigest() != content.content_hash:
            raise RagProjectionCommitError(
                f"applied content revision {revision} has an invalid content hash"
            )
        source_refs = tuple(
            _to_source_ref(document)
            for document in source_ref_documents
        )
        return KnowledgeExtractionSource(
            resource_id=content.resource_id,
            document_version=content.document_version,
            content_revision=revision,
            markdown=markdown,
            chunks=tuple(
                KnowledgeExtractionChunk(
                    chunk_id=source_ref.chunk_id,
                    chunk_index=chunk_index,
                    section_id=source_ref.section_id,
                    section_path=source_ref.section_path,
                    raw_text="\n\n".join(
                        markdown[span.start_offset : span.end_offset]
                        for span in source_ref.source_spans
                    ),
                    source_spans=source_ref.source_spans,
                )
                for chunk_index, source_ref in enumerate(source_refs)
            ),
            source_refs=source_refs,
        )

    async def load_applied_reading_blocks(
        self,
        *,
        resource_id: str,
        reading_block_ids: Sequence[str],
    ) -> tuple[RagSectionReadingBlock, ...]:
        checkpoint = await self.get_checkpoint(resource_id)
        if checkpoint is None or checkpoint.applied_content_revision is None:
            return ()

        unique_ids = tuple(dict.fromkeys(reading_block_ids))
        if not unique_ids:
            return ()
        documents = await RagSectionReadingBlockDocument.find(
            RagSectionReadingBlockDocument.content_revision
            == checkpoint.applied_content_revision,
            In(RagSectionReadingBlockDocument.block_id, unique_ids),
        ).to_list()
        by_id = {document.block_id: document for document in documents}
        return tuple(
            _to_reading_block(by_id[block_id])
            for block_id in unique_ids
            if block_id in by_id
        )

    async def load_applied_section_reading_blocks(
        self,
        *,
        resource_id: str,
        section_ids: Sequence[str],
    ) -> tuple[RagSectionReadingBlock, ...]:
        checkpoint = await self.get_checkpoint(resource_id)
        if checkpoint is None or checkpoint.applied_content_revision is None:
            return ()
        requested_ids = tuple(dict.fromkeys(section_ids))
        if not requested_ids:
            return ()
        requested_ranks = {section_id: index for index, section_id in enumerate(requested_ids)}
        documents = await RagSectionReadingBlockDocument.find(
            RagSectionReadingBlockDocument.content_revision
            == checkpoint.applied_content_revision,
            In(RagSectionReadingBlockDocument.section_id, requested_ids),
        ).to_list()
        documents.sort(
            key=lambda document: (
                requested_ranks[document.section_id],
                document.ordinal,
            )
        )
        return tuple(_to_reading_block(document) for document in documents)

    async def load_applied_sources(
        self,
        *,
        resource_id: str,
        ref_ids: Sequence[str],
    ) -> tuple[RagMaterializedSource, ...]:
        checkpoint = await self.get_checkpoint(resource_id)
        if checkpoint is None or checkpoint.applied_content_revision is None:
            return ()

        unique_ref_ids = tuple(dict.fromkeys(ref_ids))
        if not unique_ref_ids:
            return ()
        revision = checkpoint.applied_content_revision
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
        part_indexes = sorted(
            {
                part_index
                for document in ordered_documents
                for span in document.source_spans
                for part_index in _part_indexes(span.start_offset, span.end_offset)
            }
        )
        content_parts = (
            await RagContentPartDocument.find(
                RagContentPartDocument.content_revision == revision,
                In(RagContentPartDocument.part_index, part_indexes),
            )
            .sort("part_index")
            .to_list()
            if part_indexes
            else []
        )
        return tuple(
            RagMaterializedSource(
                source_ref=_to_source_ref(document),
                content=_read_source_spans(content_parts, document.source_spans),
            )
            for document in ordered_documents
        )

    async def load_applied_section_views(
        self,
        *,
        resource_id: str,
        section_ids: Sequence[str],
    ) -> tuple[RagSectionView, ...]:
        checkpoint = await self.get_checkpoint(resource_id)
        if checkpoint is None or checkpoint.applied_content_revision is None:
            return ()
        requested_ids = tuple(dict.fromkeys(section_ids))
        if not requested_ids:
            return ()
        revision = checkpoint.applied_content_revision
        documents = (
            await RagSectionDocument.find(
                RagSectionDocument.content_revision == revision,
                RagSectionDocument.resource_id == resource_id,
            )
            .sort("own_start")
            .to_list()
        )
        by_id = {document.section_id: document for document in documents}
        children_by_parent: dict[str | None, list[RagSectionDocument]] = {}
        for document in documents:
            children_by_parent.setdefault(document.parent_section_id, []).append(document)

        views: list[RagSectionView] = []
        for section_id in requested_ids:
            current = by_id.get(section_id)
            if current is None:
                continue
            siblings = children_by_parent.get(current.parent_section_id, [])
            previous = next(
                (item for item in siblings if item.ordinal == current.ordinal - 1),
                None,
            )
            next_section = next(
                (item for item in siblings if item.ordinal == current.ordinal + 1),
                None,
            )
            views.append(
                RagSectionView(
                    section=_to_section(current),
                    parent=(
                        _to_section(by_id[current.parent_section_id])
                        if current.parent_section_id is not None
                        else None
                    ),
                    previous=_to_section(previous) if previous is not None else None,
                    next=(
                        _to_section(next_section)
                        if next_section is not None
                        else None
                    ),
                    children=tuple(
                        _to_section(child)
                        for child in children_by_parent.get(current.section_id, [])
                    ),
                )
            )
        return tuple(views)

    async def _replace_revision(
        self,
        content_revision: str,
        projection: RagContentProjection,
    ) -> None:
        await RagContentRevisionDocument.find(
            RagContentRevisionDocument.content_revision == content_revision
        ).delete()
        await RagContentPartDocument.find(
            RagContentPartDocument.content_revision == content_revision
        ).delete()
        await RagSectionDocument.find(
            RagSectionDocument.content_revision == content_revision
        ).delete()
        await RagSectionReadingBlockDocument.find(
            RagSectionReadingBlockDocument.content_revision == content_revision
        ).delete()
        await RagSourceRefDocument.find(
            RagSourceRefDocument.content_revision == content_revision
        ).delete()

        await RagContentRevisionDocument(
            content_revision=content_revision,
            resource_id=projection.resource_id,
            document_version=projection.document_version,
            content_hash=projection.content_hash,
        ).insert()
        content_parts = _content_part_documents(content_revision, projection.markdown)
        if content_parts:
            await RagContentPartDocument.insert_many(content_parts)
        if projection.sections:
            await RagSectionDocument.insert_many(
                _section_document(content_revision, section)
                for section in projection.sections
            )
        if projection.reading_blocks:
            await RagSectionReadingBlockDocument.insert_many(
                _reading_block_document(content_revision, block)
                for block in projection.reading_blocks
            )
        if projection.source_refs:
            await RagSourceRefDocument.insert_many(
                _source_ref_document(content_revision, source_ref)
                for source_ref in projection.source_refs
            )


def _section_document(
    content_revision: str,
    section: RagSectionNode,
) -> RagSectionDocument:
    return RagSectionDocument(
        content_revision=content_revision,
        section_id=section.section_id,
        resource_id=section.resource_id,
        document_version=section.document_version,
        title=section.title,
        level=section.level,
        parent_section_id=section.parent_section_id,
        ordinal=section.ordinal,
        section_path=list(section.section_path),
        summary=section.summary,
        own_start=section.own_start,
        own_end=section.own_end,
        subtree_end=section.subtree_end,
    )


def _reading_block_document(
    content_revision: str,
    block: RagSectionReadingBlock,
) -> RagSectionReadingBlockDocument:
    return RagSectionReadingBlockDocument(
        content_revision=content_revision,
        block_id=block.block_id,
        section_id=block.section_id,
        ordinal=block.ordinal,
        raw_text=block.raw_text,
        source_spans=_span_documents(block.source_spans),
        page_labels=list(block.page_labels),
        anchor_labels=list(block.anchor_labels),
    )


def _source_ref_document(
    content_revision: str,
    source_ref: RagSourceRef,
) -> RagSourceRefDocument:
    return RagSourceRefDocument(
        content_revision=content_revision,
        ref_id=source_ref.ref_id,
        resource_id=source_ref.resource_id,
        document_version=source_ref.document_version,
        chunk_id=source_ref.chunk_id,
        section_id=source_ref.section_id,
        section_path=list(source_ref.section_path),
        source_spans=_span_documents(source_ref.source_spans),
        page_label=source_ref.page_label,
        anchor_labels=list(source_ref.anchor_labels),
    )


def _span_documents(
    spans: tuple[SourceSpan, ...],
) -> list[RagSourceSpanDocument]:
    return [
        RagSourceSpanDocument(
            start_offset=span.start_offset,
            end_offset=span.end_offset,
        )
        for span in spans
    ]


def _to_section(document: RagSectionDocument) -> RagSectionNode:
    return RagSectionNode(
        section_id=document.section_id,
        resource_id=document.resource_id,
        document_version=document.document_version,
        title=document.title,
        level=document.level,
        parent_section_id=document.parent_section_id,
        ordinal=document.ordinal,
        section_path=tuple(document.section_path),
        summary=document.summary,
        own_start=document.own_start,
        own_end=document.own_end,
        subtree_end=document.subtree_end,
    )


def _to_reading_block(
    document: RagSectionReadingBlockDocument,
) -> RagSectionReadingBlock:
    return RagSectionReadingBlock(
        block_id=document.block_id,
        section_id=document.section_id,
        ordinal=document.ordinal,
        raw_text=document.raw_text,
        source_spans=_to_spans(document.source_spans),
        page_labels=tuple(document.page_labels),
        anchor_labels=tuple(document.anchor_labels),
    )


def _to_source_ref(document: RagSourceRefDocument) -> RagSourceRef:
    return RagSourceRef(
        ref_id=document.ref_id,
        resource_id=document.resource_id,
        document_version=document.document_version,
        chunk_id=document.chunk_id,
        section_id=document.section_id,
        section_path=tuple(document.section_path),
        source_spans=_to_spans(document.source_spans),
        page_label=document.page_label,
        anchor_labels=tuple(document.anchor_labels),
    )


def _to_spans(documents: list[RagSourceSpanDocument]) -> tuple[SourceSpan, ...]:
    return tuple(
        SourceSpan(
            start_offset=document.start_offset,
            end_offset=document.end_offset,
        )
        for document in documents
    )


def _content_part_documents(
    content_revision: str,
    markdown: str,
) -> list[RagContentPartDocument]:
    return [
        RagContentPartDocument(
            content_revision=content_revision,
            part_index=part_index,
            start_offset=start_offset,
            end_offset=end_offset,
            text=text,
        )
        for part_index, (start_offset, end_offset, text) in enumerate(
            _split_content(markdown)
        )
    ]


def _split_content(markdown: str) -> tuple[tuple[int, int, str], ...]:
    # 单片至多约 4 MB UTF-8，给 BSON 字段和索引留足空间。
    return tuple(
        (
            start_offset,
            min(start_offset + _CONTENT_PART_CHARACTERS, len(markdown)),
            markdown[start_offset : start_offset + _CONTENT_PART_CHARACTERS],
        )
        for start_offset in range(0, len(markdown), _CONTENT_PART_CHARACTERS)
    )


def _join_content_parts(documents: list[RagContentPartDocument]) -> str:
    expected_start = 0
    content: list[str] = []
    for document in documents:
        if document.start_offset != expected_start:
            raise RagProjectionCommitError(
                f"content revision {document.content_revision} has discontinuous parts"
            )
        if document.end_offset - document.start_offset != len(document.text):
            raise RagProjectionCommitError(
                f"content revision {document.content_revision} has an invalid part range"
            )
        content.append(document.text)
        expected_start = document.end_offset
    return "".join(content)


def _part_indexes(start_offset: int, end_offset: int) -> range:
    if start_offset < 0 or end_offset <= start_offset:
        raise RagProjectionCommitError("source span has an invalid range")
    return range(
        start_offset // _CONTENT_PART_CHARACTERS,
        (end_offset - 1) // _CONTENT_PART_CHARACTERS + 1,
    )


def _read_source_spans(
    documents: list[RagContentPartDocument],
    spans: list[RagSourceSpanDocument],
) -> str:
    fragments: list[str] = []
    for span in spans:
        cursor = span.start_offset
        span_fragments: list[str] = []
        for document in documents:
            if document.end_offset - document.start_offset != len(document.text):
                raise RagProjectionCommitError(
                    f"content revision {document.content_revision} has an invalid part range"
                )
            if document.end_offset <= cursor:
                continue
            if document.start_offset >= span.end_offset:
                break
            if document.start_offset > cursor:
                raise RagProjectionCommitError("content parts do not cover source span")

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
            raise RagProjectionCommitError("content parts do not cover source span")
        fragments.append("".join(span_fragments))
    return "\n\n".join(fragments)
