"""Qdrant retrieval index 的写入 adapter。"""

import asyncio
from collections.abc import Mapping, Sequence
from hashlib import sha256
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from qdrant_client import AsyncQdrantClient
from qdrant_client import models as qdrant_models

from rag.domain.models.acl import ResourceAcl
from rag.domain.models.provenance import SourceRef
from rag.domain.models.retrieval import RetrievalChunk
from rag.domain.repositories.qdrant.retrieval_index_writer import RetrievalIndexWriter


class QdrantRetrievalIndexWriter(RetrievalIndexWriter):
    """写入 staged retrieval points，并按 revision 控制召回可见性。"""

    def __init__(
        self,
        *,
        client: AsyncQdrantClient,
        collection_name: str,
        dense_vector_size: int,
        embedding_profile: str,
        dense_vector_name: str = "dense",
        sparse_vector_name: str = "sparse",
        bm25_options: Mapping[str, Any] | None = None,
    ) -> None:
        if dense_vector_size <= 0:
            raise ValueError("dense_vector_size must be positive")
        if not collection_name.strip():
            raise ValueError("collection_name must not be empty")
        if not embedding_profile.strip():
            raise ValueError("embedding_profile must not be empty")
        if not dense_vector_name.strip() or not sparse_vector_name.strip():
            raise ValueError("vector names must not be empty")

        self._client = client
        self._collection_name = collection_name
        self._dense_vector_size = dense_vector_size
        self._embedding_profile = embedding_profile.strip()
        self._dense_vector_name = dense_vector_name
        self._sparse_vector_name = sparse_vector_name
        self._bm25_options = dict(bm25_options or {})
        self._collection_lock = asyncio.Lock()
        self._collection_ready = False

    async def load_reusable_vectors(
        self,
        *,
        resource_id: str,
        chunks: Sequence[RetrievalChunk],
    ) -> dict[str, list[float]]:
        if not chunks or not await self._client.collection_exists(
            self._collection_name
        ):
            return {}

        chunk_keys = {
            chunk.chunk_id: self._embedding_key(chunk.index_text) for chunk in chunks
        }
        vectors_by_key: dict[str, list[float]] = {}
        offset = None
        while True:
            records, offset = await self._client.scroll(
                collection_name=self._collection_name,
                scroll_filter=qdrant_models.Filter(
                    must=[
                        _match("resource_id", resource_id),
                        qdrant_models.FieldCondition(
                            key="embedding_key",
                            match=qdrant_models.MatchAny(
                                any=list(dict.fromkeys(chunk_keys.values()))
                            ),
                        ),
                    ]
                ),
                limit=256,
                offset=offset,
                with_payload=["embedding_key"],
                with_vectors=[self._dense_vector_name],
            )
            for record in records:
                payload = record.payload or {}
                embedding_key = payload.get("embedding_key")
                vectors = record.vector
                vector = (
                    vectors.get(self._dense_vector_name)
                    if isinstance(vectors, Mapping)
                    else None
                )
                if (
                    isinstance(embedding_key, str)
                    and isinstance(vector, Sequence)
                    and not isinstance(vector, (str, bytes))
                    and len(vector) == self._dense_vector_size
                ):
                    vectors_by_key.setdefault(embedding_key, list(vector))
            if offset is None:
                break

        return {
            chunk_id: vectors_by_key[embedding_key]
            for chunk_id, embedding_key in chunk_keys.items()
            if embedding_key in vectors_by_key
        }

    async def write_staged_revision(
        self,
        *,
        resource_id: str,
        content_revision: str,
        chunks: Sequence[RetrievalChunk],
        source_refs: Sequence[SourceRef],
        dense_vectors: Mapping[str, Sequence[float]],
        resource_acl: ResourceAcl,
    ) -> None:
        if not chunks:
            return

        # chunk 唯一性、source_ref 归属、向量覆盖均由 constructor 流水线保证，这里直接组装。
        source_refs_by_chunk = {
            source_ref.chunk_id: source_ref for source_ref in source_refs
        }
        await self.ensure_collection()

        points = [
            qdrant_models.PointStruct(
                id=_point_id(content_revision, chunk.chunk_id),
                vector={
                    self._dense_vector_name: list(dense_vectors[chunk.chunk_id]),
                    self._sparse_vector_name: qdrant_models.Document(
                        text=chunk.index_text,
                        model="qdrant/bm25",
                        options=self._bm25_options or None,
                    ),
                },
                payload=_to_payload(
                    resource_id=resource_id,
                    content_revision=content_revision,
                    chunk=chunk,
                    source_ref=source_refs_by_chunk[chunk.chunk_id],
                    embedding_key=self._embedding_key(chunk.index_text),
                    resource_acl=resource_acl,
                    active=False,
                ),
            )
            for chunk in chunks
        ]

        await self._client.upsert(
            collection_name=self._collection_name,
            points=points,
            wait=True,
        )

    async def activate_revision(
        self,
        *,
        resource_id: str,
        content_revision: str,
    ) -> None:
        if not await self._client.collection_exists(self._collection_name):
            return

        await self._client.set_payload(
            collection_name=self._collection_name,
            payload={"active": True},
            points=qdrant_models.Filter(
                must=[
                    _match("resource_id", resource_id),
                    _match("content_revision", content_revision),
                ]
            ),
            wait=True,
        )
        # 先关闭旧 revision 的召回可见性，再由清理步骤删除其 points。
        await self._client.set_payload(
            collection_name=self._collection_name,
            payload={"active": False},
            points=qdrant_models.Filter(
                must=[_match("resource_id", resource_id)],
                must_not=[_match("content_revision", content_revision)],
            ),
            wait=True,
        )

    async def delete_other_revisions(
        self,
        *,
        resource_id: str,
        keep_content_revision: str,
    ) -> None:
        if not await self._client.collection_exists(self._collection_name):
            return
        await self._client.delete(
            collection_name=self._collection_name,
            points_selector=qdrant_models.FilterSelector(
                filter=qdrant_models.Filter(
                    must=[_match("resource_id", resource_id)],
                    must_not=[_match("content_revision", keep_content_revision)],
                )
            ),
            wait=True,
        )

    async def delete_resources(self, resource_ids: Sequence[str]) -> None:
        ids = list(dict.fromkeys(resource_ids))
        if not ids or not await self._client.collection_exists(self._collection_name):
            return
        await self._client.delete(
            collection_name=self._collection_name,
            points_selector=qdrant_models.FilterSelector(
                filter=qdrant_models.Filter(
                    must=[
                        qdrant_models.FieldCondition(
                            key="resource_id",
                            match=qdrant_models.MatchAny(any=ids),
                        )
                    ]
                )
            ),
            wait=True,
        )

    async def ensure_collection(self) -> None:
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

    def _embedding_key(self, index_text: str) -> str:
        return sha256(f"{self._embedding_profile}\0{index_text}".encode()).hexdigest()


def _point_id(content_revision: str, chunk_id: str) -> str:
    """为 revision 和 chunk 生成可重试复用的 Qdrant point ID。"""
    return str(
        uuid5(
            NAMESPACE_URL,
            f"wisepen-rag-v2-retrieval-chunk:{content_revision}:{chunk_id}",
        )
    )


def _to_payload(
    *,
    resource_id: str,
    content_revision: str,
    chunk: RetrievalChunk,
    source_ref: SourceRef,
    embedding_key: str,
    resource_acl: ResourceAcl,
    active: bool,
) -> dict[str, Any]:
    """序列化召回、权限过滤和最终回源实际消费的字段。"""
    return {
        "resource_id": resource_id,
        "content_revision": content_revision,
        "active": active,
        "chunk_id": chunk.chunk_id,
        "reading_block_id": chunk.reading_block_id,
        "section_id": chunk.section_id,
        "raw_text": chunk.raw_text,
        "section_path": list(chunk.section_path),
        "source_spans": [
            {
                "start_offset": span.start_offset,
                "end_offset": span.end_offset,
            }
            for span in chunk.source_spans
        ],
        "page_labels": list(chunk.page_labels),
        "anchor_labels": list(chunk.anchor_labels),
        "source_ref_id": source_ref.ref_id,
        "embedding_key": embedding_key,
        "acl_revision": resource_acl.acl_revision,
        "owner_id": resource_acl.owner_id,
        "readable_users": list(resource_acl.readable_users),
        "excluded_read_users": list(resource_acl.excluded_read_users),
        "group_acls": [
            {
                "group_id": group_acl.group_id,
                "is_readable": group_acl.default_readable,
                "readable_users": list(group_acl.readable_users),
                "excluded_read_users": list(group_acl.excluded_read_users),
            }
            for group_acl in resource_acl.group_acls
        ],
    }


def _payload_indexes() -> tuple[tuple[str, qdrant_models.PayloadSchemaType], ...]:
    return (
        ("resource_id", qdrant_models.PayloadSchemaType.KEYWORD),
        ("content_revision", qdrant_models.PayloadSchemaType.KEYWORD),
        ("active", qdrant_models.PayloadSchemaType.BOOL),
        ("embedding_key", qdrant_models.PayloadSchemaType.KEYWORD),
        ("acl_revision", qdrant_models.PayloadSchemaType.INTEGER),
        ("owner_id", qdrant_models.PayloadSchemaType.KEYWORD),
        ("readable_users", qdrant_models.PayloadSchemaType.KEYWORD),
        ("excluded_read_users", qdrant_models.PayloadSchemaType.KEYWORD),
        ("group_acls[].group_id", qdrant_models.PayloadSchemaType.KEYWORD),
        ("group_acls[].is_readable", qdrant_models.PayloadSchemaType.BOOL),
        ("group_acls[].readable_users", qdrant_models.PayloadSchemaType.KEYWORD),
        ("group_acls[].excluded_read_users", qdrant_models.PayloadSchemaType.KEYWORD),
    )


def _match(key: str, value: str) -> qdrant_models.FieldCondition:
    return qdrant_models.FieldCondition(
        key=key,
        match=qdrant_models.MatchValue(value=value),
    )
