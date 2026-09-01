"""Qdrant 中可由 Mongo 图谱事实重建的节点与关系投影。"""

import asyncio
from collections.abc import Mapping, Sequence
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from qdrant_client import AsyncQdrantClient
from qdrant_client import models as qdrant_models

from rag_v3.domain.acl import ResourceAcl
from rag_v3.domain.graph import (
    GraphEdgeProjection,
    GraphNodeProjection,
    graph_source_projection_id,
)
from rag_v3.domain.repositories.graph_projections import (
    GraphEdgeVectorRepository,
    GraphNodeVectorRepository,
)


class QdrantGraphNodeVectorRepository(GraphNodeVectorRepository):
    """节点只写 Dense 向量；每个点代表一个可授权的来源投影。"""

    def __init__(
        self,
        *,
        client: AsyncQdrantClient,
        collection_name: str,
        dense_vector_size: int,
        dense_vector_name: str,
    ) -> None:
        self._client = client
        self._collection_name = _require_name(collection_name, "collection_name")
        self._dense_vector_size = _require_positive(dense_vector_size, "dense_vector_size")
        self._dense_vector_name = _require_name(dense_vector_name, "dense_vector_name")
        self._collection_lock = asyncio.Lock()
        self._collection_ready = False

    async def replace_revision(
        self,
        *,
        resource_id: str,
        content_revision: str,
        nodes: Sequence[GraphNodeProjection],
        dense_vectors: Mapping[str, Sequence[float]],
        resource_acl: ResourceAcl,
    ) -> None:
        _validate_nodes(
            resource_id,
            content_revision,
            nodes,
            dense_vectors,
            resource_acl,
            self._dense_vector_size,
        )
        await self._ensure_collection()
        await self._delete_revision(
            resource_id=resource_id,
            content_revision=content_revision,
        )
        if not nodes:
            return
        await self._client.upsert(
            collection_name=self._collection_name,
            points=[
                qdrant_models.PointStruct(
                    id=_point_id("node", projection_id),
                    vector={
                        self._dense_vector_name: list(dense_vectors[projection_id]),
                    },
                    payload=_node_payload(item, resource_acl, projection_id),
                )
                for item in nodes
                for projection_id in [_node_projection_id(item)]
            ],
            wait=True,
        )

    async def is_complete(
        self,
        *,
        resource_id: str,
        content_revision: str,
        projection_ids: Sequence[str],
    ) -> bool:
        return await _is_complete(
            self._client,
            collection_name=self._collection_name,
            resource_id=resource_id,
            content_revision=content_revision,
            projection_ids=projection_ids,
        )

    async def delete_resources(self, resource_ids: Sequence[str]) -> None:
        await _delete_resources(self._client, self._collection_name, resource_ids)

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
                )
                await _create_payload_indexes(self._client, self._collection_name)
            self._collection_ready = True

    async def _delete_revision(self, *, resource_id: str, content_revision: str) -> None:
        if not content_revision:
            return
        await self._client.delete(
            collection_name=self._collection_name,
            points_selector=qdrant_models.FilterSelector(
                filter=_revision_filter(resource_id, content_revision)
            ),
            wait=True,
        )


class QdrantGraphEdgeVectorRepository(GraphEdgeVectorRepository):
    """关系同时写 Dense 与 BM25；两路只共用 point identity，不在写入时融合。"""

    def __init__(
        self,
        *,
        client: AsyncQdrantClient,
        collection_name: str,
        dense_vector_size: int,
        dense_vector_name: str,
        sparse_vector_name: str,
    ) -> None:
        self._client = client
        self._collection_name = _require_name(collection_name, "collection_name")
        self._dense_vector_size = _require_positive(dense_vector_size, "dense_vector_size")
        self._dense_vector_name = _require_name(dense_vector_name, "dense_vector_name")
        self._sparse_vector_name = _require_name(sparse_vector_name, "sparse_vector_name")
        self._collection_lock = asyncio.Lock()
        self._collection_ready = False

    async def replace_revision(
        self,
        *,
        resource_id: str,
        content_revision: str,
        edges: Sequence[GraphEdgeProjection],
        dense_vectors: Mapping[str, Sequence[float]],
        lexical_texts: Mapping[str, str],
        resource_acl: ResourceAcl,
    ) -> None:
        _validate_edges(
            resource_id,
            content_revision,
            edges,
            dense_vectors,
            lexical_texts,
            resource_acl,
            self._dense_vector_size,
        )
        await self._ensure_collection()
        await self._delete_revision(
            resource_id=resource_id,
            content_revision=content_revision,
        )
        if not edges:
            return
        await self._client.upsert(
            collection_name=self._collection_name,
            points=[
                qdrant_models.PointStruct(
                    id=_point_id("edge", projection_id),
                    vector={
                        self._dense_vector_name: list(dense_vectors[projection_id]),
                        self._sparse_vector_name: qdrant_models.Document(
                            text=lexical_texts[projection_id],
                            model="qdrant/bm25",
                            options={"tokenizer": "multilingual"},
                        ),
                    },
                    payload=_edge_payload(item, resource_acl, projection_id),
                )
                for item in edges
                for projection_id in [_edge_projection_id(item)]
            ],
            wait=True,
        )

    async def is_complete(
        self,
        *,
        resource_id: str,
        content_revision: str,
        projection_ids: Sequence[str],
    ) -> bool:
        return await _is_complete(
            self._client,
            collection_name=self._collection_name,
            resource_id=resource_id,
            content_revision=content_revision,
            projection_ids=projection_ids,
        )

    async def delete_resources(self, resource_ids: Sequence[str]) -> None:
        await _delete_resources(self._client, self._collection_name, resource_ids)

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
                await _create_payload_indexes(self._client, self._collection_name)
            self._collection_ready = True

    async def _delete_revision(self, *, resource_id: str, content_revision: str) -> None:
        if not content_revision:
            return
        await self._client.delete(
            collection_name=self._collection_name,
            points_selector=qdrant_models.FilterSelector(
                filter=_revision_filter(resource_id, content_revision)
            ),
            wait=True,
        )


def _node_projection_id(item: GraphNodeProjection) -> str:
    return graph_source_projection_id(
        target_type="node",
        target_id=item.node.node_id,
        resource_id=item.resource_id,
        content_revision=item.content_revision,
        evidence_ids=item.evidence_ids,
        producer_id=item.producer_id,
    )


def _edge_projection_id(item: GraphEdgeProjection) -> str:
    return graph_source_projection_id(
        target_type="edge",
        target_id=item.edge.edge_id,
        resource_id=item.resource_id,
        content_revision=item.content_revision,
        evidence_ids=item.evidence_ids,
        producer_id=item.producer_id,
    )


def _validate_nodes(
    resource_id: str,
    content_revision: str,
    nodes: Sequence[GraphNodeProjection],
    dense_vectors: Mapping[str, Sequence[float]],
    acl: ResourceAcl,
    dimensions: int,
) -> None:
    projection_ids = {_node_projection_id(node) for node in nodes}
    _validate_vectors(
        resource_id,
        content_revision,
        projection_ids,
        dense_vectors,
        acl,
        nodes,
        dimensions,
    )


def _validate_edges(
    resource_id: str,
    content_revision: str,
    edges: Sequence[GraphEdgeProjection],
    dense_vectors: Mapping[str, Sequence[float]],
    lexical_texts: Mapping[str, str],
    acl: ResourceAcl,
    dimensions: int,
) -> None:
    projection_ids = {_edge_projection_id(edge) for edge in edges}
    _validate_vectors(
        resource_id,
        content_revision,
        projection_ids,
        dense_vectors,
        acl,
        edges,
        dimensions,
    )
    if projection_ids != set(lexical_texts):
        raise ValueError("lexical_texts must cover exactly the graph projections")


def _validate_vectors(
    resource_id: str,
    content_revision: str,
    projection_ids: set[str],
    dense_vectors: Mapping[str, Sequence[float]],
    acl: ResourceAcl,
    items: Sequence[GraphNodeProjection | GraphEdgeProjection],
    dimensions: int,
) -> None:
    if any(item.resource_id != acl.resource_id for item in items):
        raise ValueError("resource ACL must belong to every graph projection")
    if any(
        item.resource_id != resource_id or item.content_revision != content_revision
        for item in items
    ):
        raise ValueError("graph projections must belong to the target revision")
    if projection_ids != set(dense_vectors):
        raise ValueError("dense_vectors must cover exactly the graph projections")
    if any(len(vector) != dimensions for vector in dense_vectors.values()):
        raise ValueError("dense vector size does not match collection schema")


def _node_payload(
    item: GraphNodeProjection,
    acl: ResourceAcl,
    projection_id: str,
) -> dict[str, Any]:
    return {
        "projection_id": projection_id,
        "node_id": item.node.node_id,
        "node_type": item.node.node_type.value,
        "category": item.node.category,
        **_source_payload(item, acl),
    }


def _edge_payload(
    item: GraphEdgeProjection,
    acl: ResourceAcl,
    projection_id: str,
) -> dict[str, Any]:
    return {
        "projection_id": projection_id,
        "edge_id": item.edge.edge_id,
        "source_node_id": item.edge.source_node_id,
        "target_node_id": item.edge.target_node_id,
        "relation_type": item.edge.relation_type,
        **_source_payload(item, acl),
    }


def _source_payload(
    item: GraphNodeProjection | GraphEdgeProjection,
    acl: ResourceAcl,
) -> dict[str, Any]:
    return {
        "resource_id": item.resource_id,
        "content_revision": item.content_revision,
        "producer_id": item.producer_id,
        "evidence_ids": list(item.evidence_ids),
        "filter_values": item.filter_values,
        "acl_revision": acl.acl_revision,
        "owner_id": acl.owner_id,
        "readable_users": list(acl.readable_users),
        "excluded_read_users": list(acl.excluded_read_users),
        "group_acls": [
            {
                "group_id": group.group_id,
                "default_readable": group.default_readable,
                "readable_users": list(group.readable_users),
                "excluded_read_users": list(group.excluded_read_users),
            }
            for group in acl.group_acls
        ],
    }


def _point_id(kind: str, projection_id: str) -> str:
    return str(uuid5(NAMESPACE_URL, f"wisepen-rag-v3:graph:{kind}:{projection_id}"))


def _revision_filter(resource_id: str, content_revision: str) -> qdrant_models.Filter:
    return qdrant_models.Filter(
        must=[
            _match("resource_id", resource_id),
            _match("content_revision", content_revision),
        ]
    )


async def _is_complete(
    client: AsyncQdrantClient,
    *,
    collection_name: str,
    resource_id: str,
    content_revision: str,
    projection_ids: Sequence[str],
) -> bool:
    if not projection_ids:
        return True
    if not await client.collection_exists(collection_name):
        return False
    result = await client.count(
        collection_name=collection_name,
        count_filter=_revision_filter(resource_id, content_revision),
        exact=True,
    )
    return result.count == len(set(projection_ids))


async def _delete_resources(
    client: AsyncQdrantClient,
    collection_name: str,
    resource_ids: Sequence[str],
) -> None:
    ids = list(dict.fromkeys(resource_ids))
    if not ids or not await client.collection_exists(collection_name):
        return
    await client.delete(
        collection_name=collection_name,
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


async def _create_payload_indexes(
    client: AsyncQdrantClient,
    collection_name: str,
) -> None:
    for field_name, schema in _payload_indexes():
        await client.create_payload_index(
            collection_name=collection_name,
            field_name=field_name,
            field_schema=schema,
            wait=True,
        )


def _payload_indexes() -> tuple[tuple[str, qdrant_models.PayloadSchemaType], ...]:
    return (
        ("projection_id", qdrant_models.PayloadSchemaType.KEYWORD),
        ("node_id", qdrant_models.PayloadSchemaType.KEYWORD),
        ("edge_id", qdrant_models.PayloadSchemaType.KEYWORD),
        ("source_node_id", qdrant_models.PayloadSchemaType.KEYWORD),
        ("target_node_id", qdrant_models.PayloadSchemaType.KEYWORD),
        ("relation_type", qdrant_models.PayloadSchemaType.KEYWORD),
        ("category", qdrant_models.PayloadSchemaType.KEYWORD),
        ("resource_id", qdrant_models.PayloadSchemaType.KEYWORD),
        ("content_revision", qdrant_models.PayloadSchemaType.KEYWORD),
        ("acl_revision", qdrant_models.PayloadSchemaType.INTEGER),
        ("owner_id", qdrant_models.PayloadSchemaType.KEYWORD),
        ("readable_users", qdrant_models.PayloadSchemaType.KEYWORD),
        ("excluded_read_users", qdrant_models.PayloadSchemaType.KEYWORD),
        ("group_acls[].group_id", qdrant_models.PayloadSchemaType.KEYWORD),
        ("group_acls[].default_readable", qdrant_models.PayloadSchemaType.BOOL),
        ("group_acls[].readable_users", qdrant_models.PayloadSchemaType.KEYWORD),
        ("group_acls[].excluded_read_users", qdrant_models.PayloadSchemaType.KEYWORD),
    )


def _match(key: str, value: str) -> qdrant_models.FieldCondition:
    return qdrant_models.FieldCondition(key=key, match=qdrant_models.MatchValue(value=value))


def _require_name(value: str, name: str) -> str:
    if not value.strip():
        raise ValueError(f"{name} must not be empty")
    return value


def _require_positive(value: int, name: str) -> int:
    if value <= 0:
        raise ValueError(f"{name} must be positive")
    return value
