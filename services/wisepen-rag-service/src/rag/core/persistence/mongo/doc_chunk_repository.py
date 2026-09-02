"""Beanie adapter：幂等保存并批量读取 DocChunk。"""

from collections.abc import Sequence

from pymongo import ReplaceOne

from rag.application.document.models import (
    DocChunk,
)
from rag.application.plugins.core.codecs import DocChunkMetadataCodec
from rag.domain.entities.doc_chunks import DocChunkEntity
from rag.domain.repositories.doc_chunks import DocChunkRepository


class MongoDocChunkRepository(DocChunkRepository):
    """批量保存同一 revision 的 Chunk 事实，不保存索引文本或向量。"""

    def __init__(self, *, metadata_codec: DocChunkMetadataCodec) -> None:
        self._metadata_codec = metadata_codec

    async def save_revision(self, chunks: Sequence[DocChunk]) -> None:
        if not chunks:
            return
        operations = [
            ReplaceOne(
                {"chunk_id": chunk.chunk_id},
                _to_document(chunk, self._metadata_codec),
                upsert=True,
            )
            for chunk in chunks
        ]
        collection = DocChunkEntity.get_pymongo_collection()
        for start in range(0, len(operations), 500):
            await collection.bulk_write(operations[start : start + 500])

    async def get_chunks_by_ids(self, chunk_ids: Sequence[str]) -> list[DocChunk]:
        """一次按初检并集回查，避免检索阶段按 Chunk 逐条访问 Mongo。"""
        unique_chunk_ids = list(dict.fromkeys(chunk_ids))
        if not unique_chunk_ids:
            return []
        entities = await DocChunkEntity.find(
            {"chunk_id": {"$in": unique_chunk_ids}}
        ).to_list()
        return [_to_domain(entity, self._metadata_codec) for entity in entities]

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
        return [_to_domain(entity, self._metadata_codec) for entity in entities]

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
        return [_to_domain(entity, self._metadata_codec) for entity in entities]

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
        return [_to_domain(entity, self._metadata_codec) for entity in entities]


def _to_document(
    chunk: DocChunk,
    metadata_codec: DocChunkMetadataCodec,
) -> dict[str, object]:
    return {
        "chunk_id": chunk.chunk_id,
        "resource_id": chunk.resource_id,
        "content_revision": chunk.content_revision,
        "chunk_index": chunk.chunk_index,
        "section_id": chunk.section_id,
        "section_path": chunk.section_path,
        "raw_text": chunk.raw_text,
        "source_spans": chunk.source_spans,
        "page_labels": chunk.page_labels,
        "anchor_labels": chunk.anchor_labels,
        "contextual_prefix": chunk.contextual_prefix,
        "key_terms": chunk.key_terms,
        "extracted_node_ids": chunk.extracted_node_ids,
        "metadata": metadata_codec.encode(chunk.metadata),
    }


def _to_domain(entity: DocChunkEntity, metadata_codec: DocChunkMetadataCodec) -> DocChunk:
    return DocChunk(
        chunk_id=entity.chunk_id,
        resource_id=entity.resource_id,
        content_revision=entity.content_revision,
        chunk_index=entity.chunk_index,
        section_id=entity.section_id,
        section_path=entity.section_path,
        raw_text=entity.raw_text,
        source_spans=entity.source_spans,
        page_labels=entity.page_labels,
        anchor_labels=entity.anchor_labels,
        contextual_prefix=entity.contextual_prefix,
        key_terms=entity.key_terms,
        extracted_node_ids=entity.extracted_node_ids,
        metadata=metadata_codec.decode(entity.metadata),
    )
