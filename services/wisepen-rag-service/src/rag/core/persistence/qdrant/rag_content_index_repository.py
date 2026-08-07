from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from hashlib import sha256
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from qdrant_client import AsyncQdrantClient
from qdrant_client import models as qdrant_models

from rag.application.rag.acl import RagResourceAclProjection
from rag.application.rag.ingestion import (
    RagContentProjection,
    RagProjectionStage,
)
from rag.domain.repositories import RagAclProjectionTarget, RagVectorIndexRepository


class RagVectorIndexError(RuntimeError):
    """Qdrant 内容投影不完整或不符合 collection 契约。"""


class QdrantRagVectorIndexRepository(
    RagVectorIndexRepository,
    RagAclProjectionTarget,
):
    __slots__ = (
        "_bm25_options",
        "_client",
        "_collection_lock",
        "_collection_name",
        "_collection_ready",
        "_dense_vector_name",
        "_dense_vector_size",
        "_embedding_profile",
        "_sparse_vector_name",
    )

    def __init__(
        self,
        *,
        client: AsyncQdrantClient,
        collection_name: str,
        dense_vector_size: int,
        embedding_profile: str,
        bm25_config: qdrant_models.Bm25Config,
        dense_vector_name: str,
        sparse_vector_name: str,
    ) -> None:
        if dense_vector_size <= 0:
            raise ValueError("dense_vector_size must be positive")
        if not embedding_profile.strip():
            raise ValueError("embedding_profile must not be empty")
        if client.cloud_inference is not True:
            raise ValueError("Qdrant server-side BM25 inference must be enabled")
        self._client = client
        self._collection_name = collection_name
        self._dense_vector_size = dense_vector_size
        self._embedding_profile = embedding_profile.strip()
        self._bm25_options = bm25_config.model_dump(
            mode="json",
            exclude_none=True,
        )
        self._dense_vector_name = dense_vector_name
        self._sparse_vector_name = sparse_vector_name
        self._collection_lock = asyncio.Lock()
        self._collection_ready = False

    async def load_reusable_vectors(
        self,
        projection: RagContentProjection,
    ) -> dict[str, Sequence[float]]:
        """
        - 时机：内容已经 contextualize，准备 embedding 之前。
        - 原理：根据 embedding_profile + index_text 生成 embedding_key，
        从 Qdrant 里 scroll 同资源、同 embedding_key 的旧点，拿回可复用的 dense vector。
        - 作用：避免相同检索文本重复调用 embedding 服务。
        - 限制：collection 不存在或没有 chunk 时返回空 dict"""
        if (
            not projection.retrieval_chunks
            or not await self._client.collection_exists(self._collection_name)
        ):
            return {}

        chunk_keys = {
            chunk.chunk_id: self._embedding_key(chunk.index_text)
            for chunk in projection.retrieval_chunks
        }
        vectors_by_key: dict[str, Sequence[float]] = {}
        offset = None
        while True:
            records, offset = await self._client.scroll(
                collection_name=self._collection_name,
                scroll_filter=qdrant_models.Filter(
                    must=[
                        _match("resource_id", projection.resource_id),
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
                if isinstance(embedding_key, str) and isinstance(vector, Sequence):
                    vectors_by_key.setdefault(embedding_key, vector)
            if offset is None:
                break
        return {
            chunk_id: vectors_by_key[embedding_key]
            for chunk_id, embedding_key in chunk_keys.items()
            if embedding_key in vectors_by_key
        }

    async def upsert_staged_projection(
        self,
        *,
        projection: RagContentProjection,
        stage: RagProjectionStage,
        dense_vectors: Mapping[str, Sequence[float]],
        acl_projection: RagResourceAclProjection | None,
    ) -> None:
        """ 把 staged projection 发布到 Qdrant 的核心写入。
        - 时机：缺失 embedding 已补齐，Mongo projection 已进入 staged，但还没 apply_projection。
        - 作用：把每个 retrieval chunk 写成 Qdrant point。
        每个 point 包含 dense vector、Qdrant native BM25 sparse vector，
        以及 content_revision/resource_id/chunk_id/reading_block_id/raw_text/section/source_ref_id/ACL payload
        - 限制：有 ACL projection，且 ACL 的 resource_id 必须和内容一致。
        """
        if not projection.retrieval_chunks:
            return
        if acl_projection is None:
            raise RagVectorIndexError("ACL projection is required for indexed content")
        if acl_projection.resource_id != projection.resource_id:
            raise RagVectorIndexError("ACL projection resource does not match content")

        await self.ensure_collection()
        source_refs_by_chunk = {
            source_ref.chunk_id: source_ref for source_ref in projection.source_refs
        }

        points = []
        for chunk in projection.retrieval_chunks:
            source_ref = source_refs_by_chunk.get(chunk.chunk_id)
            if source_ref is None:
                raise RagVectorIndexError(
                    f"source ref is missing for chunk {chunk.chunk_id}"
                )
            vector = dense_vectors.get(chunk.chunk_id)
            if vector is None:
                raise RagVectorIndexError(
                    f"dense vector is missing for chunk {chunk.chunk_id}"
                )
            if len(vector) != self._dense_vector_size:
                raise RagVectorIndexError(
                    f"dense vector size does not match chunk {chunk.chunk_id}"
                )
            points.append(
                qdrant_models.PointStruct(
                    id=str(
                        uuid5(
                            NAMESPACE_URL,
                            f"wisepen-rag-retrieval-chunk:{stage.content_revision}:{chunk.chunk_id}",
                        )
                    ),
                    vector={
                        self._dense_vector_name: list(vector),
                        self._sparse_vector_name: qdrant_models.Document(
                            text=chunk.index_text,
                            model="qdrant/bm25",
                            options=self._bm25_options,
                        ),
                    },
                    payload={
                        "content_revision": stage.content_revision,
                        "resource_id": projection.resource_id,
                        "chunk_id": chunk.chunk_id,
                        "reading_block_id": chunk.reading_block_id,
                        "chunk_index": chunk.chunk_index,
                        "embedding_key": self._embedding_key(chunk.index_text),
                        "raw_text": chunk.raw_text,
                        "section_id": chunk.section_id,
                        "section_path": list(chunk.section_path),
                        "anchor_labels": list(chunk.anchor_labels),
                        "source_ref_id": source_ref.ref_id,
                        **_acl_payload(acl_projection),
                    },
                )
            )

        await self._client.upsert(
            collection_name=self._collection_name,
            points=points,
            wait=True,
        )

    async def delete_other_revisions(
        self,
        *,
        resource_id: str,
        keep_content_revision: str,
    ) -> None:
        """删除同一资源下除当前 applied revision 外的旧向量。
        三个调用点分别对应：发现消息已经 applied 后补偿清理、
        并发阶段发现已 applied 后补偿清理、当前写入 apply 成功后的正常清理"""
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

    async def delete_resources(self, resource_ids: tuple[str, ...]) -> None:
        """ 通过资源删除 kafka consumer 调用
        consumer 中 逐个 target 调 delete_resources。
        这里删除这些 resource 的全部 Qdrant points，不分 revision
        """
        unique_resource_ids = tuple(dict.fromkeys(resource_ids))
        if (
            not unique_resource_ids
            or not await self._client.collection_exists(self._collection_name)
        ):
            return
        await self._client.delete(
            collection_name=self._collection_name,
            points_selector=qdrant_models.FilterSelector(
                filter=qdrant_models.Filter(
                    must=[
                        qdrant_models.FieldCondition(
                            key="resource_id",
                            match=qdrant_models.MatchAny(
                                any=list(unique_resource_ids)
                            ),
                        )
                    ]
                )
            ),
            wait=True,
        )

    async def update_acl_projection(
        self,
        projection: RagResourceAclProjection,
    ) -> None:
        """通过 ACL 重算 consumer 调用，更新 Qdrant points 的 ACL 信息"""
        if not await self._client.collection_exists(self._collection_name):
            return
        
        # 更新同资源、且 acl_revision <= 新 revision 的点，避免旧 ACL 事件覆盖新 ACL payload。
        await self._client.set_payload(
            collection_name=self._collection_name,
            payload=_acl_payload(projection),
            points=qdrant_models.Filter(
                must=[
                    _match("resource_id", projection.resource_id),
                    qdrant_models.FieldCondition(
                        key="acl_revision",
                        range=qdrant_models.Range(lte=projection.acl_revision),
                    ),
                ]
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
            for field_name, field_schema in (
                ("resource_id", qdrant_models.PayloadSchemaType.KEYWORD),
                ("content_revision", qdrant_models.PayloadSchemaType.KEYWORD),
                ("chunk_id", qdrant_models.PayloadSchemaType.KEYWORD),
                ("section_id", qdrant_models.PayloadSchemaType.KEYWORD),
                ("embedding_key", qdrant_models.PayloadSchemaType.KEYWORD),
                ("acl_revision", qdrant_models.PayloadSchemaType.INTEGER),
                ("owner_id", qdrant_models.PayloadSchemaType.KEYWORD),
                ("readable_users", qdrant_models.PayloadSchemaType.KEYWORD),
                ("excluded_read_users", qdrant_models.PayloadSchemaType.KEYWORD),
                (
                    "computed_group_acls[].group_id",
                    qdrant_models.PayloadSchemaType.KEYWORD,
                ),
                (
                    "computed_group_acls[].is_readable",
                    qdrant_models.PayloadSchemaType.BOOL,
                ),
                (
                    "computed_group_acls[].readable_users",
                    qdrant_models.PayloadSchemaType.KEYWORD,
                ),
                (
                    "computed_group_acls[].excluded_read_users",
                    qdrant_models.PayloadSchemaType.KEYWORD,
                ),
            ):
                await self._client.create_payload_index(
                    collection_name=self._collection_name,
                    field_name=field_name,
                    field_schema=field_schema,
                    wait=True,
                )
            self._collection_ready = True

    def _embedding_key(self, index_text: str) -> str:
        """只要 embedding_profile 和 index_text 相同即可复用 embedding_key"""
        value = f"{self._embedding_profile}\0{index_text}"
        return sha256(value.encode("utf-8")).hexdigest()


def _acl_payload(projection: RagResourceAclProjection) -> dict[str, Any]:
    return {
        "acl_revision": projection.acl_revision,
        "owner_id": projection.owner_id,
        "readable_users": list(projection.readable_users),
        "excluded_read_users": list(projection.excluded_read_users),
        "computed_group_acls": [
            {
                "group_id": item.group_id,
                "is_readable": item.is_readable,
                "readable_users": list(item.readable_users),
                "excluded_read_users": list(item.excluded_read_users),
            }
            for item in projection.computed_group_acls
        ],
    }


def _match(key: str, value: str) -> qdrant_models.FieldCondition:
    return qdrant_models.FieldCondition(
        key=key,
        match=qdrant_models.MatchValue(value=value),
    )
