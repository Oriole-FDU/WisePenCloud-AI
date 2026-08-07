from __future__ import annotations

from beanie.operators import In
from rag.utils.chunkers import SourceSpan
from rag.application.rag.ingestion import (
    RagContentProjection,
    RagPageRange,
    RagProjectionStage,
    RagProjectionStageAction,
    RagSectionNode,
    RagSectionReadingBlock,
    RagSourceRef,
    prepare_projection_stage,
)
from rag.domain.entities.rag_content import (
    RagContentPartDocument,
    RagContextIndexingDocument,
    RagContentRevisionDocument,
    RagGraphExtractionDocument,
    RagPageDocument,
    RagProjectionCheckpointDocument,
    RagSectionDocument,
    RagSectionReadingBlockDocument,
    RagSourceRefDocument,
    RagSourceSpanDocument,
)
from rag.domain.repositories import RagContentProjectionRepository

from .version_repository import load_content_checkpoint

CONTENT_PART_CHARACTERS = 1_000_000


class MongoRagContentProjectionWriter(RagContentProjectionRepository):
    """写入正文投影 revision，并通过 checkpoint 发布 applied revision。"""

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
        await RagContextIndexingDocument.find(
            In(RagContextIndexingDocument.resource_id, unique_resource_ids)
        ).delete()
        await RagGraphExtractionDocument.find(
            In(RagGraphExtractionDocument.resource_id, unique_resource_ids)
        ).delete()
        if not content_revisions:
            return
        for document_type in (
            RagContentPartDocument,
            RagPageDocument,
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
        checkpoint = await load_content_checkpoint(projection.resource_id)
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

        checkpoint = await load_content_checkpoint(stage.resource_id)
        if (
            checkpoint is not None
            and checkpoint.applied_content_revision == stage.content_revision
        ):
            return
        raise RuntimeError(
            f"content revision {stage.content_revision} is no longer staged"
        )

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
        await RagPageDocument.find(
            RagPageDocument.content_revision == content_revision
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
        if projection.pages:
            await RagPageDocument.insert_many(
                _page_document(content_revision, page)
                for page in projection.pages
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
        preview=section.preview,
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
        page_labels=list(source_ref.page_labels),
        anchor_labels=list(source_ref.anchor_labels),
    )


def _page_document(
    content_revision: str,
    page: RagPageRange,
) -> RagPageDocument:
    return RagPageDocument(
        content_revision=content_revision,
        page_index=page.page_index,
        page_label=page.page_label,
        start_offset=page.start_offset,
        end_offset=page.end_offset,
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
            min(start_offset + CONTENT_PART_CHARACTERS, len(markdown)),
            markdown[start_offset : start_offset + CONTENT_PART_CHARACTERS],
        )
        for start_offset in range(0, len(markdown), CONTENT_PART_CHARACTERS)
    )
