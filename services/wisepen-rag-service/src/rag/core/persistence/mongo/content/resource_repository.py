from __future__ import annotations

from collections.abc import Sequence

from beanie.operators import In
from rag.utils.chunkers import SourceSpan
from rag.application.rag.ingestion import (
    RagSectionNode,
    RagSectionReadingBlock,
)
from rag.application.rag.resource_snapshot import (
    RagResourceContentItem,
    RagResourceContentReadResult,
    RagResourceContentWindow,
    RagResourceSnapshot,
    RagResourceSnapshotPage,
    RagResourceSnapshotSection,
)
from rag.application.rag.section_navigation import RagSectionView
from rag.domain.entities.rag_content import (
    RagContentPartDocument,
    RagContentRevisionDocument,
    RagPageDocument,
    RagSectionDocument,
    RagSectionReadingBlockDocument,
    RagSourceSpanDocument,
)
from rag.domain.repositories import (
    RagResourceSnapshotRepository,
    RagSectionNavigationRepository,
)

from .common import (
    load_applied_content_revision,
    read_source_spans,
    to_reading_block,
    to_spans,
)


class MongoRagSectionNavigationRepository(RagSectionNavigationRepository):
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
                    section=_to_section(current),
                    parent=(
                        _to_section(context_by_id[current.parent_section_id])
                        if current.parent_section_id in context_by_id
                        else None
                    ),
                    previous=_to_section(previous) if previous is not None else None,
                    next=_to_section(next_section) if next_section is not None else None,
                    children=tuple(
                        _to_section(child)
                        for child in children_by_parent.get(current.section_id, [])
                    ),
                )
            )
        return tuple(views)


class MongoRagResourceSnapshotRepository(RagResourceSnapshotRepository):
    """资源副本的文档结构与读取。"""

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

        page_documents = (
            await RagPageDocument.find(
                RagPageDocument.content_revision == revision
            )
            .sort("page_index")
            .to_list()
        )
        section_documents = (
            await RagSectionDocument.find(
                RagSectionDocument.content_revision == revision
            )
            .sort("level", "ordinal")
            .to_list()
        )
        total_length = await self._load_total_length(revision)
        return RagResourceSnapshot(
            resource_id=content.resource_id,
            document_version=content.document_version,
            content_revision=revision,
            total_length=total_length,
            pages=tuple(
                RagResourceSnapshotPage(
                    page_label=document.page_label,
                )
                for document in page_documents
            ),
            sections=_build_snapshot_section_tree(section_documents),
        )

    async def read_applied_page_content(
        self,
        *,
        resource_id: str,
        page_labels: Sequence[str],
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

        unique_page_labels = tuple(dict.fromkeys(page_labels))
        page_documents = (
            await RagPageDocument.find(
                RagPageDocument.content_revision == revision,
                In(RagPageDocument.page_label, unique_page_labels),
            )
            .sort("page_index")
            .to_list()
        )
        pages_by_label: dict[str, list[RagPageDocument]] = {}
        for page_document in page_documents:
            pages_by_label.setdefault(page_document.page_label, []).append(page_document)

        section_documents = (
            await RagSectionDocument.find(
                RagSectionDocument.content_revision == revision
            )
            .sort("level", "ordinal")
            .to_list()
        )
        reading_block_documents = (
            await RagSectionReadingBlockDocument.find(
                RagSectionReadingBlockDocument.content_revision == revision
            )
            .sort("section_id", "ordinal")
            .to_list()
        )

        items: list[RagResourceContentItem] = []
        for page_label in unique_page_labels:
            pages = pages_by_label.get(page_label, [])
            if not pages:
                items.append(
                    RagResourceContentItem(
                        key=page_label,
                        kind="page",
                        reason="page_not_found",
                    )
                )
                continue

            windows = [
                _page_window(
                    content_parts,
                    page,
                    [
                        section
                        for section in section_documents
                        if section.own_start < page.end_offset
                        and section.own_end > page.start_offset
                    ],
                    [
                        block
                        for block in reading_block_documents
                        if _reading_block_overlaps(block, page)
                    ],
                )
                for page in pages
            ]
            items.append(
                RagResourceContentItem(
                    key=page_label,
                    kind="page",
                    windows=tuple(windows),
                )
            )
        return RagResourceContentReadResult(
            resource_id=resource_id,
            content_revision=revision,
            document_version=content.document_version,
            items=tuple(items),
        )

    async def read_applied_section_content(
        self,
        *,
        resource_id: str,
        section_ids: Sequence[str],
    ) -> RagResourceContentReadResult | None:
        revision = await load_applied_content_revision(resource_id)
        if revision is None:
            return None

        content = await RagContentRevisionDocument.find_one(
            RagContentRevisionDocument.content_revision == revision
        )
        if content is None:
            return None

        unique_section_ids = tuple(dict.fromkeys(section_ids))
        section_documents = await RagSectionDocument.find(
            RagSectionDocument.content_revision == revision,
            In(RagSectionDocument.section_id, unique_section_ids),
        ).to_list()
        sections_by_id = {
            document.section_id: document for document in section_documents
        }
        block_documents = (
            await RagSectionReadingBlockDocument.find(
                RagSectionReadingBlockDocument.content_revision == revision,
                In(RagSectionReadingBlockDocument.section_id, unique_section_ids),
            )
            .sort("section_id", "ordinal")
            .to_list()
        )
        blocks_by_section: dict[str, list[RagSectionReadingBlockDocument]] = {}
        for block_document in block_documents:
            blocks_by_section.setdefault(block_document.section_id, []).append(
                block_document
            )

        items: list[RagResourceContentItem] = []
        for section_id in unique_section_ids:
            section = sections_by_id.get(section_id)
            if section is None:
                items.append(
                    RagResourceContentItem(
                        key=section_id,
                        kind="section",
                        reason="section_not_found",
                    )
                )
                continue

            windows = tuple(
                _section_block_window(section, block_document)
                for block_document in blocks_by_section.get(section_id, [])
            )
            items.append(
                RagResourceContentItem(
                    key=section_id,
                    kind="section",
                    reason="section_empty" if not windows else None,
                    windows=windows,
                )
            )
        return RagResourceContentReadResult(
            resource_id=resource_id,
            content_revision=revision,
            document_version=content.document_version,
            items=tuple(items),
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


def _build_snapshot_section_tree(
    documents: list[RagSectionDocument],
) -> tuple[RagResourceSnapshotSection, ...]:
    children_by_parent: dict[str | None, list[RagSectionDocument]] = {}
    for document in documents:
        children_by_parent.setdefault(document.parent_section_id, []).append(document)
    for children in children_by_parent.values():
        children.sort(key=lambda section: section.ordinal)

    def to_snapshot_section(document: RagSectionDocument) -> RagResourceSnapshotSection:
        return RagResourceSnapshotSection(
            section_id=document.section_id,
            title=document.title,
            level=document.level,
            section_path=tuple(document.section_path),
            has_content=document.own_end > document.own_start,
            children=tuple(
                to_snapshot_section(child)
                for child in children_by_parent.get(document.section_id, [])
            ),
        )

    return tuple(
        to_snapshot_section(document)
        for document in children_by_parent.get(None, [])
    )


def _section_block_window(
    section: RagSectionDocument,
    block: RagSectionReadingBlockDocument,
) -> RagResourceContentWindow:
    source_spans = to_spans(block.source_spans)
    return RagResourceContentWindow(
        text=block.raw_text,
        start_offset=source_spans[0].start_offset,
        end_offset=source_spans[-1].end_offset,
        source_spans=source_spans,
        page_labels=tuple(block.page_labels),
        section_paths=(tuple(section.section_path),),
        anchor_labels=tuple(block.anchor_labels),
        metadata={
            "section_id": section.section_id,
            "section_path": list(section.section_path),
            "title": section.title,
            "block_id": block.block_id,
            "ordinal": block.ordinal,
        },
    )


def _reading_block_overlaps(
    block: RagSectionReadingBlockDocument,
    page: RagPageDocument,
) -> bool:
    return any(
        span.start_offset < page.end_offset and span.end_offset > page.start_offset
        for span in block.source_spans
    )


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
        preview=document.preview,
        own_start=document.own_start,
        own_end=document.own_end,
        subtree_end=document.subtree_end,
    )


def _read_content_range(
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


def _page_window(
    documents: list[RagContentPartDocument],
    page: RagPageDocument,
    section_documents: list[RagSectionDocument],
    reading_block_documents: list[RagSectionReadingBlockDocument],
    *,
    max_chars: int | None = None,
) -> RagResourceContentWindow:
    start_offset = page.start_offset
    end_offset = page.end_offset
    if max_chars is not None:
        end_offset = min(end_offset, start_offset + max(max_chars, 0))
    text = _read_content_range(
        documents,
        start_offset=start_offset,
        end_offset=end_offset,
    )
    return RagResourceContentWindow(
        text=text,
        start_offset=start_offset,
        end_offset=end_offset,
        source_spans=(
            (SourceSpan(start_offset, end_offset),)
            if start_offset < end_offset
            else ()
        ),
        page_labels=(page.page_label,),
        section_paths=tuple(
            dict.fromkeys(
                tuple(document.section_path)
                for document in section_documents
                if document.section_path
            )
        ),
        anchor_labels=tuple(
            dict.fromkeys(
                anchor_label
                for block in reading_block_documents
                for anchor_label in block.anchor_labels
            )
        ),
        metadata={"page_label": page.page_label},
    )
