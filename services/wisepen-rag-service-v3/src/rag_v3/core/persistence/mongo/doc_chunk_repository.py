"""Beanie adapter：幂等保存并批量读取 DocChunk。"""

from collections.abc import Sequence

from common.utils.document import SourceSpan
from pymongo import ReplaceOne

from rag_v3.domain.entities.doc_chunks import (
    DocChunkEntity,
    StoredChunkMetadata,
)
from rag_v3.domain.entities.documents import StoredSpan
from rag_v3.domain.models import DocChunk, GeneralChunkMetadata
from rag_v3.domain.repositories.doc_chunks import DocChunkRepository


class MongoDocChunkRepository(DocChunkRepository):
    """批量保存同一 revision 的 Chunk 事实，不保存索引文本或向量。"""

    async def save_revision(self, chunks: Sequence[DocChunk]) -> None:
        if not chunks:
            return
        operations = [
            ReplaceOne(
                {"chunk_id": chunk.chunk_id},
                _to_document(chunk),
                upsert=True,
            )
            for chunk in chunks
        ]
        await DocChunkEntity.get_pymongo_collection().bulk_write(operations)

    async def get_revision_chunks(
        self,
        *,
        resource_id: str,
        content_revision: str,
    ) -> list[DocChunk]:
        entities = (
            await DocChunkEntity.find(
                {
                    "resource_id": resource_id,
                    "content_revision": content_revision,
                }
            )
            .sort("+chunk_index")
            .to_list()
        )
        return [_to_domain(entity) for entity in entities]

    async def get_chunks_by_ids(self, chunk_ids: Sequence[str]) -> list[DocChunk]:
        """一次按初检并集回查，避免检索阶段按 Chunk 逐条访问 Mongo。"""
        unique_chunk_ids = list(dict.fromkeys(chunk_ids))
        if not unique_chunk_ids:
            return []
        entities = await DocChunkEntity.find(
            {"chunk_id": {"$in": unique_chunk_ids}}
        ).to_list()
        return [_to_domain(entity) for entity in entities]

    async def get_revisions_chunks(
        self,
        revisions: Sequence[tuple[str, str]],
    ) -> list[DocChunk]:
        """批量装载父块装箱所需的 revision 全量 Chunk，避免按资源循环。"""
        unique_revisions = list(dict.fromkeys(revisions))
        if not unique_revisions:
            return []
        entities = await DocChunkEntity.find(
            {
                "$or": [
                    {"resource_id": resource_id, "content_revision": content_revision}
                    for resource_id, content_revision in unique_revisions
                ]
            }
        ).sort("+chunk_index").to_list()
        return [_to_domain(entity) for entity in entities]

    async def get_section_chunks(
        self,
        *,
        resource_id: str,
        content_revision: str,
        section_ids: Sequence[str],
    ) -> list[DocChunk]:
        unique_section_ids = list(dict.fromkeys(section_ids))
        if not unique_section_ids:
            return []
        entities = (
            await DocChunkEntity.find(
                {
                    "resource_id": resource_id,
                    "content_revision": content_revision,
                    "section_id": {"$in": unique_section_ids},
                }
            )
            .sort("+chunk_index")
            .to_list()
        )
        return [_to_domain(entity) for entity in entities]


def _to_document(chunk: DocChunk) -> dict[str, object]:
    return {
        "chunk_id": chunk.chunk_id,
        "resource_id": chunk.resource_id,
        "content_revision": chunk.content_revision,
        "chunk_index": chunk.chunk_index,
        "section_id": chunk.section_id,
        "section_path": list(chunk.section_path),
        "raw_text": chunk.raw_text,
        "source_spans": [
            StoredSpan(
                start_offset=span.start_offset,
                end_offset=span.end_offset,
            )
            for span in chunk.source_spans
        ],
        "page_labels": list(chunk.page_labels),
        "anchor_labels": list(chunk.anchor_labels),
        "contextual_prefix": chunk.contextual_prefix,
        "key_terms": list(chunk.key_terms),
        "extracted_node_ids": list(chunk.extracted_node_ids),
        "metadata": StoredChunkMetadata(chunk_type=chunk.metadata.chunk_type),
    }


def _to_domain(entity: DocChunkEntity) -> DocChunk:
    return DocChunk(
        chunk_id=entity.chunk_id,
        resource_id=entity.resource_id,
        content_revision=entity.content_revision,
        chunk_index=entity.chunk_index,
        section_id=entity.section_id,
        section_path=tuple(entity.section_path),
        raw_text=entity.raw_text,
        source_spans=tuple(
            SourceSpan(span.start_offset, span.end_offset)
            for span in entity.source_spans
        ),
        page_labels=tuple(entity.page_labels),
        anchor_labels=tuple(entity.anchor_labels),
        contextual_prefix=entity.contextual_prefix,
        key_terms=tuple(entity.key_terms),
        extracted_node_ids=tuple(entity.extracted_node_ids),
        metadata=GeneralChunkMetadata(chunk_type=entity.metadata.chunk_type),
    )
