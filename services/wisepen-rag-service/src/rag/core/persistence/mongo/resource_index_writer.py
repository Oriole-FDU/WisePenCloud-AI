"""Resource index writer backed by Beanie entities."""

from collections.abc import Sequence
from dataclasses import dataclass

from common.utils.document import OutlineNode, SourceSpan
from pymongo.errors import DuplicateKeyError

from rag.domain.entities import (
    ContentRevisionEntity,
    ReadingBlockEntity,
    ResourceIndexStateEntity,
    SectionEntity,
    SourcePartEntity,
    SourceRefEntity,
)
from rag.domain.models.content import (
    ContentRevision,
    ReadingBlock,
)
from rag.domain.models.provenance import SourceRef
from rag.domain.models.structure import DocumentStructure, Section
from rag.domain.repositories.mongo.resource_index_writer import (
    ResourceIndexWriter,
    StageAction,
)

from ._source_parts import SourcePart, split_source_parts


@dataclass(slots=True)
class _ResourceIndexState:
    """Mongo 发布状态机的内部 revision 指针投影。"""

    resource_id: str
    staged_content_revision: str | None = None
    staged_document_version: int | None = None
    applied_content_revision: str | None = None
    applied_document_version: int | None = None


class MongoResourceIndexWriter(ResourceIndexWriter):
    """通过 Beanie entities 管理 staged/applied 内容 revision。"""

    async def stage_revision(
        self,
        revision: ContentRevision,
        markdown: str,
        structure: DocumentStructure,
        outline: list[OutlineNode],
        reading_blocks: Sequence[ReadingBlock],
        source_refs: Sequence[SourceRef],
    ) -> StageAction:
        # revision/结构一致性由 constructor 流水线保证，这里直接落库。
        action = _decide_stage(revision, await self._get_state(revision.resource_id))
        if action is not StageAction.STAGED:
            return action

        stored_revision = ContentRevisionEntity(
            **_revision_document(revision, structure, outline)
        )
        existing_revision = await ContentRevisionEntity.find_one(
            {"content_revision": revision.content_revision}
        )
        if existing_revision is None:
            await stored_revision.insert()
        else:
            stored_revision.id = existing_revision.id
            await stored_revision.replace()

        await SourcePartEntity.find(
            {"content_revision": revision.content_revision}
        ).delete_many()
        parts = split_source_parts(
            resource_id=revision.resource_id,
            content_revision=revision.content_revision,
            markdown=markdown,
        )
        if parts:
            await SourcePartEntity.insert_many(
                [SourcePartEntity(**_source_part_document(part)) for part in parts]
            )
        for entity_type in (SectionEntity, ReadingBlockEntity, SourceRefEntity):
            await entity_type.find(
                {"content_revision": revision.content_revision}
            ).delete_many()
        if structure.sections:
            await SectionEntity.insert_many(
                [
                    SectionEntity(**_section_document(revision, section))
                    for section in structure.sections
                ]
            )
        if reading_blocks:
            await ReadingBlockEntity.insert_many(
                [
                    ReadingBlockEntity(**_reading_block_document(revision, block))
                    for block in reading_blocks
                ]
            )
        if source_refs:
            await SourceRefEntity.insert_many(
                [
                    SourceRefEntity(**_source_ref_document(revision, source_ref))
                    for source_ref in source_refs
                ]
            )

        state_collection = ResourceIndexStateEntity.get_pymongo_collection()
        try:
            result = await state_collection.update_one(
                _stage_state_filter(revision),
                {
                    "$set": {
                        "staged_content_revision": revision.content_revision,
                        "staged_document_version": revision.document_version,
                    },
                    "$setOnInsert": {"resource_id": revision.resource_id},
                },
                upsert=True,
            )
        except DuplicateKeyError:
            latest = await self._get_state(revision.resource_id)
            action = _decide_stage(revision, latest)
            if action is not StageAction.STAGED:
                return action
            result = await state_collection.update_one(
                _stage_state_filter(revision),
                {
                    "$set": {
                        "staged_content_revision": revision.content_revision,
                        "staged_document_version": revision.document_version,
                    }
                },
            )
        if result.matched_count == 0 and result.upserted_id is None:
            latest = await self._get_state(revision.resource_id)
            action = _decide_stage(revision, latest)
            if action is not StageAction.STAGED:
                return action
            raise RuntimeError(
                f"resource {revision.resource_id} stage changed concurrently"
            )
        return StageAction.STAGED

    async def apply_revision(self, revision: ContentRevision) -> None:
        result = await ResourceIndexStateEntity.get_pymongo_collection().update_one(
            {
                "resource_id": revision.resource_id,
                "staged_content_revision": revision.content_revision,
                "staged_document_version": revision.document_version,
            },
            {
                "$set": {
                    "applied_content_revision": revision.content_revision,
                    "applied_document_version": revision.document_version,
                    "staged_content_revision": None,
                    "staged_document_version": None,
                }
            },
        )
        if result.modified_count == 1:
            return
        state = await self._get_state(revision.resource_id)
        if (
            state is not None
            and state.applied_content_revision == revision.content_revision
        ):
            return
        raise RuntimeError(
            f"content revision {revision.content_revision} is no longer staged"
        )

    @staticmethod
    async def _get_state(resource_id: str) -> _ResourceIndexState | None:
        entity = await ResourceIndexStateEntity.find_one({"resource_id": resource_id})
        if entity is None:
            return None
        return _to_resource_index_state(entity)

    async def delete_other_revisions(
        self,
        *,
        resource_id: str,
        keep_content_revision: str,
    ) -> None:
        revisions = await ContentRevisionEntity.find(
            {
                "resource_id": resource_id,
                "content_revision": {"$ne": keep_content_revision},
            }
        ).to_list()
        revision_ids = [revision.content_revision for revision in revisions]
        if not revision_ids:
            return
        for entity_type in (
            SourcePartEntity,
            SectionEntity,
            ReadingBlockEntity,
            SourceRefEntity,
            ContentRevisionEntity,
        ):
            await entity_type.find(
                {"content_revision": {"$in": revision_ids}}
            ).delete_many()

    async def clear_resource_states(self, resource_ids: Sequence[str]) -> None:
        ids = list(dict.fromkeys(resource_ids))
        if not ids:
            return
        await ResourceIndexStateEntity.find({"resource_id": {"$in": ids}}).delete_many()

    async def delete_resources(self, resource_ids: Sequence[str]) -> None:
        ids = list(dict.fromkeys(resource_ids))
        if not ids:
            return
        revisions = await ContentRevisionEntity.find(
            {"resource_id": {"$in": ids}}
        ).to_list()
        revision_ids = [entity.content_revision for entity in revisions]
        if not revision_ids:
            return
        for entity_type in (
            SourcePartEntity,
            SectionEntity,
            ReadingBlockEntity,
            SourceRefEntity,
            ContentRevisionEntity,
        ):
            await entity_type.find(
                {"content_revision": {"$in": revision_ids}}
            ).delete_many()


def _to_resource_index_state(record: ResourceIndexStateEntity) -> _ResourceIndexState:
    return _ResourceIndexState(
        resource_id=record.resource_id,
        staged_content_revision=record.staged_content_revision,
        staged_document_version=record.staged_document_version,
        applied_content_revision=record.applied_content_revision,
        applied_document_version=record.applied_document_version,
    )


def _revision_document(
    revision: ContentRevision,
    structure: DocumentStructure,
    outline: list[OutlineNode],
) -> dict[str, object]:
    return {
        "resource_id": revision.resource_id,
        "content_revision": revision.content_revision,
        "document_version": revision.document_version,
        "content_hash": revision.content_hash,
        "index_schema_version": revision.index_schema_version,
        "structure_mode": structure.mode.value,
        "total_length": structure.total_length,
        "outline": outline,
        "pages": [
            {
                "page_index": page.page_index,
                "page_label": page.page_label,
                "start_offset": page.source_span.start_offset,
                "end_offset": page.source_span.end_offset,
            }
            for page in structure.pages
        ],
        "anchors": [
            {
                "label": anchor.label,
                "start_offset": anchor.source_span.start_offset,
                "end_offset": anchor.source_span.end_offset,
            }
            for anchor in structure.anchors
        ],
    }


def _source_part_document(part: SourcePart) -> dict[str, object]:
    return {
        "resource_id": part.resource_id,
        "content_revision": part.content_revision,
        "part_index": part.part_index,
        "start_offset": part.source_span.start_offset,
        "end_offset": part.source_span.end_offset,
        "text": part.text,
    }


def _section_document(
    revision: ContentRevision,
    section: Section,
) -> dict[str, object]:
    return {
        "resource_id": revision.resource_id,
        "content_revision": revision.content_revision,
        "section_id": section.section_id,
        "title": section.title,
        "level": section.level,
        "parent_section_id": section.parent_section_id,
        "ordinal": section.ordinal,
        "section_path": list(section.section_path),
        "preview": section.preview,
        "own_start": section.own_span.start_offset,
        "own_end": section.own_span.end_offset,
        "subtree_end": section.subtree_span.end_offset,
        "content_spans": [_span_document(span) for span in section.content_spans],
    }


def _reading_block_document(
    revision: ContentRevision,
    block: ReadingBlock,
) -> dict[str, object]:
    return {
        "resource_id": revision.resource_id,
        "content_revision": revision.content_revision,
        "block_id": block.block_id,
        "section_ids": list(block.section_ids),
        "ordinal": block.ordinal,
        "raw_text": block.raw_text,
        "source_spans": [_span_document(span) for span in block.source_spans],
        "start_offset": block.source_spans[0].start_offset,
        "end_offset": block.source_spans[-1].end_offset,
        "page_labels": list(block.page_labels),
        "anchor_labels": list(block.anchor_labels),
    }


def _source_ref_document(
    revision: ContentRevision,
    source_ref: SourceRef,
) -> dict[str, object]:
    return {
        "resource_id": revision.resource_id,
        "content_revision": revision.content_revision,
        "ref_id": source_ref.ref_id,
        "chunk_id": source_ref.chunk_id,
        "reading_block_id": source_ref.reading_block_id,
        "section_id": source_ref.section_id,
        "section_path": list(source_ref.section_path),
        "source_spans": [_span_document(span) for span in source_ref.source_spans],
        "page_labels": list(source_ref.page_labels),
        "anchor_labels": list(source_ref.anchor_labels),
    }


def _span_document(span: SourceSpan) -> dict[str, int]:
    return {"start_offset": span.start_offset, "end_offset": span.end_offset}


def _stage_state_filter(revision: ContentRevision) -> dict[str, object]:
    return {
        "resource_id": revision.resource_id,
        "$and": [
            {
                "$or": [
                    {"applied_document_version": {"$exists": False}},
                    {"applied_document_version": None},
                    {"applied_document_version": {"$lte": revision.document_version}},
                ]
            },
            {
                "$or": [
                    {"staged_document_version": {"$exists": False}},
                    {"staged_document_version": None},
                    {"staged_document_version": {"$lte": revision.document_version}},
                ]
            },
        ],
    }


def _decide_stage(
    revision: ContentRevision,
    state: _ResourceIndexState | None,
) -> StageAction:
    if state is None:
        return StageAction.STAGED
    if state.applied_content_revision == revision.content_revision:
        return StageAction.ALREADY_APPLIED
    if (
        state.applied_document_version is not None
        and state.applied_document_version > revision.document_version
    ):
        return StageAction.STALE
    if (
        state.staged_document_version is not None
        and state.staged_document_version > revision.document_version
    ):
        return StageAction.STALE
    return StageAction.STAGED
