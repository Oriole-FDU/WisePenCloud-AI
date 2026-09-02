"""Qdrant 中文档 Chunk 的 Dense 与 BM25 投影。"""

from collections.abc import Mapping, Sequence
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from qdrant_client import AsyncQdrantClient
from qdrant_client import models as qdrant_models

from rag.application.document.models import DocChunk
from rag.core.persistence.qdrant.common import (
    QdrantVectorRepository,
    match_value,
    permission_filter,
)
from rag.domain.acl import PermissionScope, ResourceAcl
from rag.domain.repositories.document_vectors import (
    DocumentVectorRepository,
    VectorCandidate,
)

_UPSERT_BATCH_SIZE = 256

_PAYLOAD_INDEXES = (
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
    ("group_acls[].excluded_read_users", qdrant_models.PayloadSchemaType.KEYWORD),
)


class QdrantDocumentVectorRepository(QdrantVectorRepository, DocumentVectorRepository):
    """管理文档检索投影；正文始终以 Mongo DocChunk 为准。"""

    _payload_indexes = _PAYLOAD_INDEXES

    def __init__(
        self,
        *,
        client: AsyncQdrantClient,
        collection_name: str,
        dense_vector_size: int,
        dense_vector_name: str,
        sparse_vector_name: str,
    ) -> None:
        super().__init__(
            client=client,
            collection_name=collection_name,
            dense_vector_size=dense_vector_size,
            dense_vector_name=dense_vector_name,
        )
        self._sparse_vector_name = sparse_vector_name

    async def write_revision(
        self,
        *,
        chunks: Sequence[DocChunk],
        dense_vectors: Mapping[str, Sequence[float]],
        resource_acl: ResourceAcl,
    ) -> None:
        if not chunks:
            return

        await self._ensure_collection(
            sparse_vectors_config={
                self._sparse_vector_name: qdrant_models.SparseVectorParams(
                    modifier=qdrant_models.Modifier.IDF,
                )
            }
        )

        points = [
            _to_point(
                chunk=chunk,
                dense_vector=dense_vectors[chunk.chunk_id],
                resource_acl=resource_acl,
                dense_vector_name=self._dense_vector_name,
                sparse_vector_name=self._sparse_vector_name,
            )
            for chunk in chunks
        ]

        # 限制单次请求体大小，避免大文档触发传输超时或报文上限
        for start in range(0, len(points), _UPSERT_BATCH_SIZE):
            await self._client.upsert(
                collection_name=self._collection_name,
                points=points[start : start + _UPSERT_BATCH_SIZE],
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
            count_filter=qdrant_models.Filter(
                must=[
                    match_value("resource_id", resource_id),
                    match_value("content_revision", content_revision),
                ]
            ),
            exact=True,
        )
        return result.count == len(set(chunk_ids))

    async def search_dense(
        self,
        *,
        query_vector: Sequence[float],
        scope: PermissionScope,
        limit: int,
    ) -> list[VectorCandidate]:
        """只执行 Dense 初检；与 BM25 独立取 Top-N，禁止在 Qdrant 内融合。"""
        if not await self._client.collection_exists(self._collection_name):
            return []

        response = await self._client.query_points(
            collection_name=self._collection_name,
            query=list(query_vector),
            using=self._dense_vector_name,
            query_filter=permission_filter(scope),
            limit=limit,
            with_payload=["chunk_id", "resource_id", "content_revision"],
        )
        return [
            _parse_candidate(point.payload, dense_rank=index)
            for index, point in enumerate(response.points, start=1)
        ]

    async def search_bm25(
        self,
        *,
        query: str,
        scope: PermissionScope,
        limit: int,
    ) -> list[VectorCandidate]:
        """只执行 BM25 初检；候选之后再与 Dense 并集并交给 reranker。"""
        if not await self._client.collection_exists(self._collection_name):
            return []

        response = await self._client.query_points(
            collection_name=self._collection_name,
            query=qdrant_models.Document(
                text=query,
                model="qdrant/bm25",
                options={"tokenizer": "multilingual"},
            ),
            using=self._sparse_vector_name,
            query_filter=permission_filter(scope),
            limit=limit,
            with_payload=["chunk_id", "resource_id", "content_revision"],
        )
        return [
            _parse_candidate(point.payload, lexical_rank=index)
            for index, point in enumerate(response.points, start=1)
        ]


# Point / Payload 序列化与转换

def _point_id(chunk: DocChunk) -> str:
    """同一 revision/Chunk 始终写同一个 point，重试只能覆盖不会重复。"""
    return str(
        uuid5(
            NAMESPACE_URL,
            f"wisepen-rag-v3:document:{chunk.content_revision}:{chunk.chunk_id}",
        )
    )


def _to_point(
    chunk: DocChunk,
    dense_vector: Sequence[float],
    resource_acl: ResourceAcl,
    dense_vector_name: str,
    sparse_vector_name: str,
) -> qdrant_models.PointStruct:
    """构造 Qdrant 写入点位。"""
    return qdrant_models.PointStruct(
        id=_point_id(chunk),
        vector={
            dense_vector_name: list(dense_vector),
            sparse_vector_name: qdrant_models.Document(
                text=chunk.get_lexical_text(),
                model="qdrant/bm25",
                options={"tokenizer": "multilingual"},
            ),
        },
        payload=_payload(chunk, resource_acl),
    )


def _payload(chunk: DocChunk, resource_acl: ResourceAcl) -> dict[str, Any]:
    """只序列化候选定位、ACL 预过滤与后续图谱 seed 所需字段。"""
    return {
        "chunk_id": chunk.chunk_id,
        "resource_id": chunk.resource_id,
        "content_revision": chunk.content_revision,
        "section_id": chunk.section_id,
        "section_path": chunk.section_path,
        "extracted_node_ids": chunk.extracted_node_ids,
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


def _parse_candidate(
    payload: Mapping[str, object] | None,
    *,
    dense_rank: int | None = None,
    lexical_rank: int | None = None,
) -> VectorCandidate:
    if payload is None:
        raise ValueError("Qdrant candidate payload is missing")

    values = [
        payload.get(key) for key in ("chunk_id", "resource_id", "content_revision")
    ]
    if not all(isinstance(value, str) and value for value in values):
        raise ValueError("Qdrant candidate payload is incomplete")

    chunk_id, resource_id, content_revision = values
    return VectorCandidate(
        chunk_id=chunk_id,
        resource_id=resource_id,
        content_revision=content_revision,
        dense_rank=dense_rank,
        lexical_rank=lexical_rank,
    )
