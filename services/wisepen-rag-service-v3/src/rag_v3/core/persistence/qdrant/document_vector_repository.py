"""Qdrant 中文档 Chunk 的 Dense 与 BM25 投影。"""

import asyncio
from collections.abc import Mapping, Sequence
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from qdrant_client import AsyncQdrantClient
from qdrant_client import models as qdrant_models

from rag_v3.domain.acl import ResourceAcl
from rag_v3.domain.models import DocChunk
from rag_v3.domain.repositories.document_vectors import DocumentVectorRepository


class QdrantDocumentVectorRepository(DocumentVectorRepository):
    """管理文档检索投影；正文始终以 Mongo DocChunk 为准。"""

    def __init__(
        self,
        *,
        client: AsyncQdrantClient,
        collection_name: str,
        dense_vector_size: int,
        dense_vector_name: str,
        sparse_vector_name: str,
    ) -> None:
        if dense_vector_size <= 0:
            raise ValueError("dense_vector_size must be positive")
        if not collection_name.strip():
            raise ValueError("collection_name must not be empty")
        if not dense_vector_name.strip() or not sparse_vector_name.strip():
            raise ValueError("vector names must not be empty")

        self._client = client
        self._collection_name = collection_name
        self._dense_vector_size = dense_vector_size
        self._dense_vector_name = dense_vector_name
        self._sparse_vector_name = sparse_vector_name
        self._collection_lock = asyncio.Lock()
        self._collection_ready = False

    async def write_revision(
        self,
        *,
        chunks: Sequence[DocChunk],
        dense_vectors: Mapping[str, Sequence[float]],
        resource_acl: ResourceAcl,
    ) -> None:
        if not chunks:
            return
        if any(chunk.resource_id != resource_acl.resource_id for chunk in chunks):
            raise ValueError("resource ACL must belong to every indexed chunk")
        if {chunk.chunk_id for chunk in chunks} != set(dense_vectors):
            raise ValueError("dense_vectors must cover exactly the indexed chunks")
        if any(len(vector) != self._dense_vector_size for vector in dense_vectors.values()):
            raise ValueError("dense vector size does not match collection schema")

        await self._ensure_collection()
        await self._client.upsert(
            collection_name=self._collection_name,
            points=[
                qdrant_models.PointStruct(
                    id=_point_id(chunk),
                    vector={
                        self._dense_vector_name: list(dense_vectors[chunk.chunk_id]),
                        self._sparse_vector_name: qdrant_models.Document(
                            text=chunk.get_lexical_text(),
                            model="qdrant/bm25",
                            options={"tokenizer": "multilingual"},
                        ),
                    },
                    payload=_payload(chunk, resource_acl),
                )
                for chunk in chunks
            ],
            wait=True,
        )

    async def is_complete(
        self,
        *,
        resource_id: str,
        content_revision: str,
        chunk_ids: Sequence[str],
    ) -> bool:
        if not chunk_ids:
            return True
        if not await self._client.collection_exists(self._collection_name):
            return False
        result = await self._client.count(
            collection_name=self._collection_name,
            count_filter=_revision_filter(resource_id, content_revision),
            exact=True,
        )
        return result.count == len(set(chunk_ids))

    async def _ensure_collection(self) -> None:
        if self._collection_ready:
            return
        async with self._collection_lock:
            if self._collection_ready:
                return
            if not await self._client.collection_exists(self._collection_name):
                await self._client.create_collection(
                    collection_name=self._collection_name,
                    vectors_config={
                        self._dense_vector_name: qdrant_models.VectorParams(
                            size=self._dense_vector_size,
                            distance=qdrant_models.Distance.COSINE,
                        )
                    },
                    sparse_vectors_config={
                        self._sparse_vector_name: qdrant_models.SparseVectorParams(
                            modifier=qdrant_models.Modifier.IDF,
                        )
                    },
                )
            for field_name, field_schema in _payload_indexes():
                await self._client.create_payload_index(
                    collection_name=self._collection_name,
                    field_name=field_name,
                    field_schema=field_schema,
                    wait=True,
                )
            self._collection_ready = True


def _point_id(chunk: DocChunk) -> str:
    """同一 revision/Chunk 始终写同一个 point，重试只能覆盖不会重复。"""
    return str(
        uuid5(
            NAMESPACE_URL,
            f"wisepen-rag-v3:document:{chunk.content_revision}:{chunk.chunk_id}",
        )
    )


def _payload(chunk: DocChunk, resource_acl: ResourceAcl) -> dict[str, Any]:
    """只序列化候选定位、ACL 预过滤与后续图谱 seed 所需字段。"""
    return {
        "chunk_id": chunk.chunk_id,
        "resource_id": chunk.resource_id,
        "content_revision": chunk.content_revision,
        "section_id": chunk.section_id,
        "section_path": list(chunk.section_path),
        "extracted_node_ids": list(chunk.extracted_node_ids),
        "acl_revision": resource_acl.acl_revision,
        "owner_id": resource_acl.owner_id,
        "readable_users": list(resource_acl.readable_users),
        "excluded_read_users": list(resource_acl.excluded_read_users),
        "group_acls": [
            {
                "group_id": group_acl.group_id,
                "default_readable": group_acl.default_readable,
                "readable_users": list(group_acl.readable_users),
                "excluded_read_users": list(group_acl.excluded_read_users),
            }
            for group_acl in resource_acl.group_acls
        ],
    }


def _revision_filter(
    resource_id: str,
    content_revision: str,
) -> qdrant_models.Filter:
    return qdrant_models.Filter(
        must=[
            _match("resource_id", resource_id),
            _match("content_revision", content_revision),
        ]
    )


def _payload_indexes() -> tuple[tuple[str, qdrant_models.PayloadSchemaType], ...]:
    return (
        ("chunk_id", qdrant_models.PayloadSchemaType.KEYWORD),
        ("resource_id", qdrant_models.PayloadSchemaType.KEYWORD),
        ("content_revision", qdrant_models.PayloadSchemaType.KEYWORD),
        ("section_id", qdrant_models.PayloadSchemaType.KEYWORD),
        ("extracted_node_ids", qdrant_models.PayloadSchemaType.KEYWORD),
        ("acl_revision", qdrant_models.PayloadSchemaType.INTEGER),
        ("owner_id", qdrant_models.PayloadSchemaType.KEYWORD),
        ("readable_users", qdrant_models.PayloadSchemaType.KEYWORD),
        ("excluded_read_users", qdrant_models.PayloadSchemaType.KEYWORD),
        ("group_acls[].group_id", qdrant_models.PayloadSchemaType.KEYWORD),
        ("group_acls[].default_readable", qdrant_models.PayloadSchemaType.BOOL),
        ("group_acls[].readable_users", qdrant_models.PayloadSchemaType.KEYWORD),
        (
            "group_acls[].excluded_read_users",
            qdrant_models.PayloadSchemaType.KEYWORD,
        ),
    )


def _match(key: str, value: str) -> qdrant_models.FieldCondition:
    return qdrant_models.FieldCondition(
        key=key,
        match=qdrant_models.MatchValue(value=value),
    )
