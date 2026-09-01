"""Beanie adapter：单文档保存和批量读取 Document revision。"""

from collections.abc import Sequence

from common.utils.document import Anchor, Page, Section, SourceSpan

from rag_v3.domain.entities.documents import (
    DocumentRevisionEntity,
    StoredAnchor,
    StoredDocumentMetadata,
    StoredPage,
    StoredSection,
    StoredSpan,
)
from rag_v3.domain.models import (
    ContentRevision,
    Document,
    DocumentStructure,
    GeneralDocumentMetadata,
)
from rag_v3.domain.repositories.documents import DocumentRepository


class MongoDocumentRepository(DocumentRepository):
    """把 RAG Document 投影为一条 Mongo revision 文档。"""

    async def save_revision(self, document: Document) -> None:
        # 相同 content_revision 必须对应同一正文哈希；replace 是幂等重试，非迁移逻辑。
        await DocumentRevisionEntity.get_pymongo_collection().update_one(
            {
                "resource_id": document.resource_id,
                "content_revision": document.revision.content_revision,
            },
            {"$set": _to_document(document)},
            upsert=True,
        )

    async def exists(self, *, resource_id: str, content_revision: str) -> bool:
        entity = await DocumentRevisionEntity.find_one(
            {
                "resource_id": resource_id,
                "content_revision": content_revision,
            }
        )
        return entity is not None

    async def get_revisions(
        self,
        revisions: Sequence[tuple[str, str]],
    ) -> dict[tuple[str, str], Document]:
        unique_revisions = list(dict.fromkeys(revisions))
        if not unique_revisions:
            return {}

        clauses = [
            {"resource_id": resource_id, "content_revision": content_revision}
            for resource_id, content_revision in unique_revisions
        ]
        entities = await DocumentRevisionEntity.find({"$or": clauses}).to_list()
        return {
            (entity.resource_id, entity.content_revision): _to_domain(entity)
            for entity in entities
        }

    async def find_by_section_ids(
        self,
        section_ids: Sequence[str],
    ) -> list[Document]:
        unique_section_ids = list(dict.fromkeys(section_ids))
        if not unique_section_ids:
            return []
        entities = await DocumentRevisionEntity.find(
            {"sections.section_id": {"$in": unique_section_ids}}
        ).to_list()
        return [_to_domain(entity) for entity in entities]


def _to_document(document: Document) -> dict[str, object]:
    return {
        "resource_id": document.resource_id,
        "content_revision": document.revision.content_revision,
        "document_version": document.revision.document_version,
        "content_sha256": document.revision.content_sha256,
        "raw_content": document.raw_content,
        "total_length": document.structure.total_length,
        "sections": [_stored_section(section) for section in document.structure.sections],
        "pages": [_stored_page(page) for page in document.structure.pages],
        "anchors": [_stored_anchor(anchor) for anchor in document.structure.anchors],
        "metadata": StoredDocumentMetadata(
            document_type=document.metadata.document_type,
        ),
    }


def _stored_span(span: SourceSpan) -> StoredSpan:
    return StoredSpan(start_offset=span.start_offset, end_offset=span.end_offset)


def _stored_section(section: Section) -> StoredSection:
    return StoredSection(
        section_id=section.section_id,
        title=section.title,
        level=section.level,
        parent_section_id=section.parent_section_id,
        ordinal=section.ordinal,
        section_path=list(section.section_path),
        own_span=_stored_span(section.own_span),
        subtree_span=_stored_span(section.subtree_span),
        content_spans=[_stored_span(span) for span in section.content_spans],
        preview=section.preview,
    )


def _stored_page(page: Page) -> StoredPage:
    return StoredPage(page_index=page.page_index, page_label=page.page_label, source_span=_stored_span(page.source_span))


def _stored_anchor(anchor: Anchor) -> StoredAnchor:
    return StoredAnchor(label=anchor.label, source_span=_stored_span(anchor.source_span))


def _to_domain(entity: DocumentRevisionEntity) -> Document:
    return Document(
        resource_id=entity.resource_id,
        revision=ContentRevision(
            resource_id=entity.resource_id,
            document_version=entity.document_version,
            content_sha256=entity.content_sha256,
        ),
        raw_content=entity.raw_content,
        structure=DocumentStructure(
            total_length=entity.total_length,
            sections=tuple(_section(section) for section in entity.sections),
            pages=tuple(_page(page) for page in entity.pages),
            anchors=tuple(_anchor(anchor) for anchor in entity.anchors),
        ),
        metadata=GeneralDocumentMetadata(
            document_type=entity.metadata.document_type,
        ),
    )


def _span(span: StoredSpan) -> SourceSpan:
    return SourceSpan(span.start_offset, span.end_offset)


def _section(section: StoredSection) -> Section:
    return Section(
        section_id=section.section_id,
        title=section.title,
        level=section.level,
        parent_section_id=section.parent_section_id,
        ordinal=section.ordinal,
        section_path=tuple(section.section_path),
        own_span=_span(section.own_span),
        subtree_span=_span(section.subtree_span),
        content_spans=[_span(span) for span in section.content_spans],
        preview=section.preview,
    )


def _page(page: StoredPage) -> Page:
    return Page(
        page_index=page.page_index,
        page_label=page.page_label,
        source_span=_span(page.source_span),
    )


def _anchor(anchor: StoredAnchor) -> Anchor:
    return Anchor(label=anchor.label, source_span=_span(anchor.source_span))
