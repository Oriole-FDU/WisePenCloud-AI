from dataclasses import dataclass

import pytest
from qdrant_client import models as qdrant_models

from rag.core.persistence.qdrant import QdrantRetrievalIndexWriter
from rag.domain.models.acl import ResourceAcl
from rag.domain.models.provenance import SourceRef
from rag.domain.models.retrieval import RetrievalChunk
from common.utils.document import SourceSpan


@dataclass
class _Record:
    payload: dict[str, object]
    vector: dict[str, list[float]]


class _QdrantClient:
    def __init__(self, *, exists: bool = False) -> None:
        self.exists = exists
        self.created_collection = None
        self.payload_indexes = []
        self.points = []
        self.payload_updates = []
        self.deletes = []
        self.scroll_calls = []
        self.scroll_records = []

    async def collection_exists(self, collection_name):
        return self.exists

    async def create_collection(self, **kwargs):
        self.created_collection = kwargs
        self.exists = True

    async def create_payload_index(self, **kwargs):
        self.payload_indexes.append(kwargs)

    async def upsert(self, **kwargs):
        self.points = kwargs["points"]

    async def set_payload(self, **kwargs):
        self.payload_updates.append(kwargs)

    async def delete(self, **kwargs):
        self.deletes.append(kwargs)

    async def scroll(self, **kwargs):
        self.scroll_calls.append(kwargs)
        return self.scroll_records, None


def _chunk(chunk_id: str = "chunk-1") -> RetrievalChunk:
    return RetrievalChunk(
        chunk_id=chunk_id,
        reading_block_id="block-1",
        section_id="section-1",
        section_path=["Title"],
        raw_text="原文内容",
        index_text="上下文\n\n原文内容",
        source_spans=[SourceSpan(0, 4)],
        anchor_labels=["page-1"],
    )


def _source_ref(chunk_id: str = "chunk-1") -> SourceRef:
    return SourceRef(
        ref_id=f"ref-{chunk_id}",
        resource_id="resource-1",
        content_revision="revision-1",
        chunk_id=chunk_id,
        reading_block_id="block-1",
        section_id="section-1",
        section_path=["Title"],
        source_spans=[SourceSpan(0, 4)],
    )


def _acl() -> ResourceAcl:
    return ResourceAcl(
        resource_id="resource-1",
        acl_revision=3,
        owner_id="owner-1",
        readable_users=["user-1"],
        excluded_read_users=["user-2"],
    )


def _writer(client: _QdrantClient) -> QdrantRetrievalIndexWriter:
    return QdrantRetrievalIndexWriter(
        client=client,
        collection_name="retrieval-chunks",
        dense_vector_size=3,
        embedding_profile="embedding-v1",
    )


@pytest.mark.asyncio
async def test_write_initializes_collection_and_stages_contract_payload() -> None:
    client = _QdrantClient()
    await _writer(client).write_staged_revision(
        resource_id="resource-1",
        content_revision="revision-1",
        chunks=[_chunk()],
        source_refs=[_source_ref()],
        dense_vectors={"chunk-1": [0.1, 0.2, 0.3]},
        resource_acl=_acl(),
    )

    assert client.created_collection["collection_name"] == "retrieval-chunks"
    assert client.created_collection["vectors_config"]["dense"].size == 3
    assert (
        client.created_collection["sparse_vectors_config"]["sparse"].modifier
        is qdrant_models.Modifier.IDF
    )
    assert {index["field_name"] for index in client.payload_indexes} == {
        "resource_id",
        "content_revision",
        "active",
        "embedding_key",
        "acl_revision",
        "owner_id",
        "readable_users",
        "excluded_read_users",
        "group_acls[].group_id",
        "group_acls[].is_readable",
        "group_acls[].readable_users",
        "group_acls[].excluded_read_users",
    }
    point = client.points[0]
    assert point.payload["active"] is False
    assert point.payload["source_ref_id"] == "ref-chunk-1"
    assert point.payload["source_spans"] == [{"start_offset": 0, "end_offset": 4}]
    assert point.payload["page_labels"] == []
    assert "chunk_index" not in point.payload
    assert point.vector["dense"] == [0.1, 0.2, 0.3]
    assert isinstance(point.vector["sparse"], qdrant_models.Document)


@pytest.mark.asyncio
async def test_reusable_vectors_are_scoped_to_resource_and_embedding_key() -> None:
    client = _QdrantClient(exists=True)
    writer = _writer(client)
    key = writer._embedding_key(_chunk().index_text)
    client.scroll_records = [
        _Record({"embedding_key": key}, {"dense": [0.4, 0.5, 0.6]})
    ]

    vectors = await writer.load_reusable_vectors(
        resource_id="resource-1",
        chunks=[_chunk()],
    )

    assert vectors == {"chunk-1": [0.4, 0.5, 0.6]}
    assert client.scroll_calls[0]["scroll_filter"].must[0].key == "resource_id"


@pytest.mark.asyncio
async def test_activation_disables_old_revision_and_cleanup_deletes_it() -> None:
    client = _QdrantClient(exists=True)
    writer = _writer(client)

    await writer.activate_revision(
        resource_id="resource-1",
        content_revision="revision-2",
    )
    await writer.delete_other_revisions(
        resource_id="resource-1",
        keep_content_revision="revision-2",
    )

    assert client.payload_updates[0]["payload"] == {"active": True}
    assert client.payload_updates[1]["payload"] == {"active": False}
    assert (
        client.deletes[0]["points_selector"].filter.must_not[0].key
        == "content_revision"
    )
