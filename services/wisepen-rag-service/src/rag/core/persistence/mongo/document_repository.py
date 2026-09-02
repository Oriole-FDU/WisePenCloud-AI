"""Beanie adapter：单文档保存和批量读取 Document revision。"""

from collections.abc import Sequence

from rag.application.document.models import (
    ContentRevision,
    Document,
    DocumentStructure,
)
from rag.application.plugins.core.codecs import DocumentMetadataCodec
from rag.domain.entities.documents import DocumentRevisionEntity
from rag.domain.repositories.documents import DocumentRepository


class MongoDocumentRepository(DocumentRepository):
    """把 RAG Document 投影为一条 Mongo revision 文档。"""

    def __init__(self, *, metadata_codec: DocumentMetadataCodec) -> None:
        self._metadata_codec = metadata_codec

    async def save_revision(self, document: Document) -> None:
        metadata = self._metadata_codec.encode(document.metadata)
        existing = await DocumentRevisionEntity.find_one(
            {
                "resource_id": document.resource_id,
                "content_revision": document.revision.content_revision,
            }
        )
        # 相同 revision 严禁篡改 metadata
        if existing is not None and existing.metadata != metadata:
            raise ValueError("document metadata differs for the same content revision")
        await DocumentRevisionEntity.get_pymongo_collection().update_one(
            {
                "resource_id": document.resource_id,
                "content_revision": document.revision.content_revision,
            },
            {"$set": _to_document(document, metadata)},
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
            (entity.resource_id, entity.content_revision): _to_domain(entity, self._metadata_codec)
            for entity in entities
        }

    async def find_by_section_ids(
        self,
        section_ids: Sequence[str],
    ) -> list[Document]:
        """子章节反查完整文档"""
        unique_section_ids = list(dict.fromkeys(section_ids))
        if not unique_section_ids:
            return []
        entities = await DocumentRevisionEntity.find(
            {"sections.section_id": {"$in": unique_section_ids}}
        ).to_list()
        return [_to_domain(entity, self._metadata_codec) for entity in entities]


def _to_document(document: Document, metadata: dict[str, object]) -> dict[str, object]:
    return {
        "resource_id": document.resource_id,
        "content_revision": document.revision.content_revision,
        "document_version": document.revision.document_version,
        "content_sha256": document.revision.content_sha256,
        "raw_content": document.raw_content,
        "total_length": document.structure.total_length,
        "sections": document.structure.sections,
        "pages": document.structure.pages,
        "anchors": document.structure.anchors,
        "metadata": metadata,
    }


def _to_domain(entity: DocumentRevisionEntity, metadata_codec: DocumentMetadataCodec) -> Document:
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
            sections=entity.sections,
            pages=entity.pages,
            anchors=entity.anchors,
        ),
        metadata=metadata_codec.decode(entity.metadata),
    )
