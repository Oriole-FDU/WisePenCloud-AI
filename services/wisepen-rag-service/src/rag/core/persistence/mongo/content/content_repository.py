from __future__ import annotations

from collections.abc import Sequence
from hashlib import sha256

from beanie.operators import In
from common.utils.chunkers import SourceSpan
from rag.application.rag.evidence import RagMaterializedSource
from rag.application.rag.graph_extraction import (
    KnowledgeExtractionChunk,
    KnowledgeExtractionSource,
)
from rag.application.rag.ingestion import (
    RagContentLocator,
    RagSectionNode,
    RagSectionReadingBlock,
    RagSourceRef,
)
from rag.application.rag.resource_snapshot import (
    RagResourceContentReadResult,
    RagResourceContentWindow,
    RagResourceSnapshot,
)
from rag.application.rag.section_navigation import RagSectionView
from rag.domain.entities.rag_content import (
    RagContentPartDocument,
    RagContentLocatorDocument,
    RagContentRevisionDocument,
    RagSectionDocument,
    RagSectionReadingBlockDocument,
    RagSourceRefDocument,
    RagSourceSpanDocument,
)

from .version_repository import load_applied_content_revision

CONTENT_PART_CHARACTERS = 1_000_000
RESOURCE_CONTENT_READ_MAX_CHARS = 8000


def to_section(document: RagSectionDocument) -> RagSectionNode:
    return RagSectionNode(
        section_id=document.section_id,
        resource_id=document.resource_id,
        document_version=document.document_version,
        title=document.title,
        level=document.level,
        parent_section_id=document.parent_section_id,
        ordinal=document.ordinal,
        section_path=tuple(document.section_path),
        preview=document.preview,
        own_start=document.own_start,
        own_end=document.own_end,
        subtree_end=document.subtree_end,
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


def to_source_ref(document: RagSourceRefDocument) -> RagSourceRef:
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


def to_spans(documents: list[RagSourceSpanDocument]) -> tuple[SourceSpan, ...]:
    return tuple(
        SourceSpan(
            start_offset=document.start_offset,
            end_offset=document.end_offset,
        )
        for document in documents
    )


def join_content_parts(documents: list[RagContentPartDocument]) -> str:
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


def part_indexes(start_offset: int, end_offset: int) -> range:
    if start_offset < 0 or end_offset <= start_offset:
        raise RuntimeError("source span has an invalid range")
    return range(
        start_offset // CONTENT_PART_CHARACTERS,
        (end_offset - 1) // CONTENT_PART_CHARACTERS + 1,
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


def read_content_range(
    documents: list[RagContentPartDocument],
    *,
    start_offset: int,
    end_offset: int,
) -> str:
    if start_offset >= end_offset:
        return ""
    return read_source_spans(
        documents,
        [RagSourceSpanDocument(start_offset=start_offset, end_offset=end_offset)],
    )


def normalize_content_offset(
    value: int | None,
    total_length: int,
    *,
    default: int,
) -> int:
    offset = default if value is None else value
    if offset < 0:
        offset += total_length
    return min(max(offset, 0), total_length)


def locator_window(
    documents: list[RagContentPartDocument],
    target_locator: RagContentLocatorDocument,
    locator_documents: list[RagContentLocatorDocument],
    *,
    max_chars: int,
) -> RagResourceContentWindow:
    start_offset = target_locator.start_offset
    requested_end = target_locator.end_offset
    end_offset = min(requested_end, start_offset + max_chars)
    text = read_content_range(
        documents,
        start_offset=start_offset,
        end_offset=end_offset,
    )
    return RagResourceContentWindow(
        text=text,
        start_offset=start_offset,
        end_offset=end_offset,
        source_spans=(SourceSpan(start_offset, end_offset),) if start_offset < end_offset else (),
        locator_names=tuple(dict.fromkeys(document.name for document in locator_documents)),
        page_labels=_locator_labels(locator_documents, "page:"),
        section_paths=tuple(
            tuple(document.name.removeprefix("section:").split(" > "))
            for document in locator_documents
            if document.name.startswith("section:")
        ),
        anchor_labels=_locator_labels(locator_documents, "anchor:"),
        truncated=end_offset < requested_end,
    )


def _locator_labels(
    locator_documents: list[RagContentLocatorDocument],
    prefix: str,
) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            document.name.removeprefix(prefix)
            for document in locator_documents
            if document.name.startswith(prefix)
        )
    )


class MongoRagExtractionSourceRepository:
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
        source_ref_documents.sort(
            key=lambda document: (
                document.source_spans[0].start_offset,
                document.source_spans[-1].end_offset,
                document.ref_id,
            )
        )
        markdown = join_content_parts(content_parts)
        if sha256(markdown.encode("utf-8")).hexdigest() != content.content_hash:
            raise RuntimeError(
                f"applied content revision {revision} has an invalid content hash"
            )
        source_refs = tuple(
            to_source_ref(document)
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


class MongoRagSourceRepository:
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
                for part_index in part_indexes(span.start_offset, span.end_offset)
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
                source_ref=to_source_ref(document),
                content=read_source_spans(content_parts, document.source_spans),
            )
            for document in ordered_documents
        )


class MongoRagSectionNavigationRepository:
    """按 applied revision 读取 Section frontier 和 Section 正文块。"""

    async def load_applied_section_reading_blocks(
        self,
        *,
        resource_id: str,
        section_ids: Sequence[str],
    ) -> tuple[RagSectionReadingBlock, ...]:
        revision = await load_applied_content_revision(resource_id)
        if revision is None:
            return ()

        requested_ids = tuple(dict.fromkeys(section_ids))
        if not requested_ids:
            return ()
        requested_ranks = {
            section_id: index for index, section_id in enumerate(requested_ids)
        }
        documents = await RagSectionReadingBlockDocument.find(
            RagSectionReadingBlockDocument.content_revision == revision,
            In(RagSectionReadingBlockDocument.section_id, requested_ids),
        ).to_list()
        documents.sort(
            key=lambda document: (
                requested_ranks[document.section_id],
                document.ordinal,
            )
        )
        return tuple(to_reading_block(document) for document in documents)

    async def load_applied_section_views(
        self,
        *,
        resource_id: str,
        section_ids: Sequence[str],
    ) -> tuple[RagSectionView, ...]:
        revision = await load_applied_content_revision(resource_id)
        if revision is None:
            return ()

        requested_ids = tuple(dict.fromkeys(section_ids))
        if not requested_ids:
            return ()
        documents = await RagSectionDocument.find(
            RagSectionDocument.content_revision == revision,
            RagSectionDocument.resource_id == resource_id,
            In(RagSectionDocument.section_id, requested_ids),
        ).to_list()
        current_by_id = {document.section_id: document for document in documents}

        # 只加载当前节点需要的 parent、前后兄弟和 children，避免读取整棵标题树。
        parent_ids = tuple(
            dict.fromkeys(
                document.parent_section_id
                for document in documents
                if document.parent_section_id is not None
            )
        )
        sibling_conditions = [
            {
                "parent_section_id": document.parent_section_id,
                "ordinal": ordinal,
            }
            for document in documents
            for ordinal in (document.ordinal - 1, document.ordinal + 1)
            if ordinal >= 0
        ]
        context_conditions: list[dict[str, object]] = []
        if parent_ids:
            context_conditions.append({"section_id": {"$in": list(parent_ids)}})
        context_conditions.append(
            {"parent_section_id": {"$in": list(requested_ids)}}
        )
        context_conditions.extend(sibling_conditions)

        context_documents = (
            await RagSectionDocument.find(
                RagSectionDocument.content_revision == revision,
                RagSectionDocument.resource_id == resource_id,
                {"$or": context_conditions},
            ).to_list()
            if context_conditions
            else []
        )
        context_by_id = {
            document.section_id: document for document in (*documents, *context_documents)
        }
        children_by_parent: dict[str | None, list[RagSectionDocument]] = {}
        for document in context_documents:
            children_by_parent.setdefault(document.parent_section_id, []).append(
                document
            )
        for children in children_by_parent.values():
            children.sort(key=lambda document: document.ordinal)

        views: list[RagSectionView] = []
        for section_id in requested_ids:
            current = current_by_id.get(section_id)
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
                    section=to_section(current),
                    parent=(
                        to_section(context_by_id[current.parent_section_id])
                        if current.parent_section_id in context_by_id
                        else None
                    ),
                    previous=to_section(previous) if previous is not None else None,
                    next=to_section(next_section) if next_section is not None else None,
                    children=tuple(
                        to_section(child)
                        for child in children_by_parent.get(current.section_id, [])
                    ),
                )
            )
        return tuple(views)


class MongoRagResourceSnapshotRepository:
    """资源副本的 locator 快照与读取。"""

    async def load_applied_resource_snapshot(
        self,
        *,
        resource_id: str,
    ) -> RagResourceSnapshot | None:
        revision = await load_applied_content_revision(resource_id)
        if revision is None:
            return None

        content = await RagContentRevisionDocument.find_one(
            RagContentRevisionDocument.content_revision == revision
        )
        if content is None:
            return None

        locator_documents = (
            await RagContentLocatorDocument.find(
                RagContentLocatorDocument.content_revision == revision
            )
            .sort("locator_index")
            .to_list()
        )
        total_length = await self._load_total_length(revision)
        return RagResourceSnapshot(
            resource_id=content.resource_id,
            document_version=content.document_version,
            content_revision=revision,
            total_length=total_length,
            locators=tuple(
                RagContentLocator(
                    locator_index=document.locator_index,
                    name=document.name,
                    kind=document.kind,
                    start_offset=document.start_offset,
                    end_offset=document.end_offset,
                )
                for document in locator_documents
            ),
        )

    async def read_applied_resource_content(
        self,
        *,
        resource_id: str,
        locator_name: str | None = None,
        start: int | None = None,
        end: int | None = None,
        max_chars: int = RESOURCE_CONTENT_READ_MAX_CHARS,
    ) -> RagResourceContentReadResult | None:
        revision = await load_applied_content_revision(resource_id)
        if revision is None:
            return None

        content = await RagContentRevisionDocument.find_one(
            RagContentRevisionDocument.content_revision == revision
        )
        if content is None:
            return None

        content_parts = (
            await RagContentPartDocument.find(
                RagContentPartDocument.content_revision == revision
            )
            .sort("part_index")
            .to_list()
        )

        if locator_name is not None:
            locator_documents = (
                await RagContentLocatorDocument.find(
                    RagContentLocatorDocument.content_revision == revision,
                    RagContentLocatorDocument.name == locator_name,
                )
                .sort("locator_index")
                .to_list()
            )
            if not locator_documents:
                return RagResourceContentReadResult(
                    resource_id=resource_id,
                    content_revision=revision,
                    document_version=content.document_version,
                    locator_name=locator_name,
                    reason="locator_not_found",
                )
            windows: list[RagResourceContentWindow] = []
            for locator_document in locator_documents:
                overlapping_locators = (
                    await RagContentLocatorDocument.find(
                        RagContentLocatorDocument.content_revision == revision,
                        RagContentLocatorDocument.start_offset
                        < locator_document.end_offset,
                        RagContentLocatorDocument.end_offset
                        > locator_document.start_offset,
                    )
                    .sort("locator_index")
                    .to_list()
                )
                windows.append(
                    locator_window(
                        content_parts,
                        locator_document,
                        overlapping_locators,
                        max_chars=max_chars,
                    )
                )
            return RagResourceContentReadResult(
                resource_id=resource_id,
                content_revision=revision,
                document_version=content.document_version,
                locator_name=locator_name,
                windows=tuple(windows),
            )

        total_length = await self._load_total_length(revision)
        normalized_start = normalize_content_offset(start, total_length, default=0)
        requested_end = normalize_content_offset(end, total_length, default=total_length)
        if requested_end <= normalized_start:
            normalized_end = normalized_start
        else:
            normalized_end = min(requested_end, normalized_start + max_chars)
        truncated = normalized_end < requested_end

        locator_documents = (
            await RagContentLocatorDocument.find(
                RagContentLocatorDocument.content_revision == revision,
                RagContentLocatorDocument.start_offset < normalized_end,
                RagContentLocatorDocument.end_offset > normalized_start,
            )
            .sort("locator_index")
            .to_list()
        )
        text = read_content_range(
            content_parts,
            start_offset=normalized_start,
            end_offset=normalized_end,
        )
        window = RagResourceContentWindow(
            text=text,
            start_offset=normalized_start,
            end_offset=normalized_end,
            source_spans=(SourceSpan(normalized_start, normalized_end),)
            if normalized_start < normalized_end
            else (),
            locator_names=tuple(dict.fromkeys(locator.name for locator in locator_documents)),
            page_labels=_locator_labels(locator_documents, "page:"),
            section_paths=tuple(
                tuple(locator.name.removeprefix("section:").split(" > "))
                for locator in locator_documents
                if locator.name.startswith("section:")
            ),
            anchor_labels=_locator_labels(locator_documents, "anchor:"),
            truncated=truncated,
        )
        return RagResourceContentReadResult(
            resource_id=resource_id,
            content_revision=revision,
            document_version=content.document_version,
            windows=(window,),
        )

    async def _load_total_length(self, content_revision: str) -> int:
        part = (
            await RagContentPartDocument.find(
                RagContentPartDocument.content_revision == content_revision
            )
            .sort("-part_index")
            .limit(1)
            .to_list()
        )
        if not part:
            return 0
        return part[0].end_offset
