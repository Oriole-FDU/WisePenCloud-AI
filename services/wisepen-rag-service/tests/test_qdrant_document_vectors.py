"""P1-B Qdrant 文档索引适配器的 schema 和 payload 边界。"""

from dataclasses import replace
from types import SimpleNamespace

import pytest
from common.utils.document import SourceSpan

from rag.application.document.models import DocChunk
from rag.core.persistence.qdrant import QdrantDocumentVectorRepository
from rag.domain.acl import ResourceAcl


class _FakeQdrant:
    def __init__(self) -> None:
        self.exists = False
        self.created = []
        self.payload_indexes = []
        self.points = []
        self.upsert_batches = []

    async def collection_exists(self, collection_name):
        return self.exists

    async def create_collection(self, **kwargs):
        self.exists = True
        self.created.append(kwargs)

    async def create_payload_index(self, **kwargs):
        self.payload_indexes.append(kwargs)

    async def upsert(self, **kwargs):
        self.upsert_batches.append(kwargs["points"])
        self.points.extend(kwargs["points"])

    async def count(self, **kwargs):
        return SimpleNamespace(count=len(self.points))


def _chunk() -> DocChunk:
    return DocChunk(
        chunk_id="rchk_1",
        resource_id="resource",
        content_revision="resource@1#hash",
        chunk_index=0,
        section_id="rsec_1",
        section_path=("标题",),
        raw_text="正文",
        source_spans=(SourceSpan(0, 2),),
        contextual_prefix="上下文",
        key_terms=("关键词",),
    )


@pytest.mark.asyncio
async def test_qdrant_document_projection_has_two_vectors_and_minimal_payload() -> None:
    client = _FakeQdrant()
    repository = QdrantDocumentVectorRepository(
        client=client,
        collection_name="document_chunk_vectors",
        dense_vector_size=2,
        dense_vector_name="dense",
        sparse_vector_name="sparse",
    )
    chunk = _chunk()
    await repository.write_revision(
        chunks=[chunk],
        dense_vectors={chunk.chunk_id: [0.1, 0.2]},
        resource_acl=ResourceAcl(resource_id="resource", acl_revision=3, owner_id="owner"),
    )

    assert len(client.created) == 1
    assert len(client.payload_indexes) > 5
    point = client.points[0]
    assert set(point.vector) == {"dense", "sparse"}
    assert point.vector["sparse"].text == chunk.get_lexical_text()
    assert point.payload["chunk_id"] == chunk.chunk_id
    assert "raw_text" not in point.payload
    assert "contextual_prefix" not in point.payload
    assert "key_terms" not in point.payload
    assert await repository.is_complete(
        resource_id="resource",
        content_revision="resource@1#hash",
        chunk_ids=[chunk.chunk_id],
    )


@pytest.mark.asyncio
async def test_qdrant_document_projection_batches_large_revisions() -> None:
    client = _FakeQdrant()
    repository = QdrantDocumentVectorRepository(
        client=client,
        collection_name="document_chunk_vectors",
        dense_vector_size=2,
        dense_vector_name="dense",
        sparse_vector_name="sparse",
    )
    chunks = [replace(_chunk(), chunk_id=f"rchk_{index}", chunk_index=index) for index in range(513)]

    await repository.write_revision(
        chunks=chunks,
        dense_vectors={chunk.chunk_id: [0.1, 0.2] for chunk in chunks},
        resource_acl=ResourceAcl(resource_id="resource", acl_revision=3, owner_id="owner"),
    )

    assert [len(batch) for batch in client.upsert_batches] == [256, 256, 1]
    assert len(client.points) == len(chunks)
