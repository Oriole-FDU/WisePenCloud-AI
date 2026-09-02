"""Neo4j 图谱的 Node/Edge 两类 Qdrant 投影仓储。"""

from collections.abc import Mapping, Sequence
from typing import Any, Literal
from uuid import NAMESPACE_URL, uuid5

from qdrant_client import AsyncQdrantClient
from qdrant_client import models as qdrant_models

from rag.application.graph.models import (
    GraphEdgeProjection,
    GraphNodeProjection,
    graph_source_projection_id,
)
from rag.core.persistence.qdrant.common import (
    QdrantVectorRepository,
    match_any,
    match_value,
    permission_filter,
)
from rag.domain.acl import PermissionScope, ResourceAcl
from rag.domain.repositories.graph_edge_vectors import GraphEdgeVectorRepository
from rag.domain.repositories.graph_node_vectors import (
    GraphFilterCondition,
    GraphNodeVectorRepository,
    GraphVectorCandidate,
)

# --- 常量：公共 Payload 索引配置 ---

_PAYLOAD_INDEXES = (
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


# --- Node 向量仓储 ---

class QdrantGraphNodeVectorRepository(
    QdrantVectorRepository, GraphNodeVectorRepository
):
    """节点只写 Dense 向量；每个点代表一个可授权的来源投影。"""

    _payload_indexes = _PAYLOAD_INDEXES

    async def replace_revision(
        self,
        *,
        resource_id: str,
        content_revision: str,
        nodes: Sequence[GraphNodeProjection],
        dense_vectors: Mapping[str, Sequence[float]],
        resource_acl: ResourceAcl,
    ) -> None:
        await self._ensure_collection()
        await self._delete_revision(
            resource_id=resource_id, content_revision=content_revision
        )
        if not nodes:
            return

        points = [
            _to_node_point(
                item=item,
                dense_vector=dense_vectors[_node_projection_id(item)],
                resource_acl=resource_acl,
                dense_vector_name=self._dense_vector_name,
            )
            for item in nodes
        ]
        await self._client.upsert(
            collection_name=self._collection_name,
            points=points,
            wait=True,
        )

    async def search_dense(
        self,
        *,
        query_vector: Sequence[float],
        scope: PermissionScope,
        resource_ids: Sequence[str] | None,
        node_categories: Sequence[str],
        metadata_filters: Sequence[GraphFilterCondition],
        limit: int,
    ) -> list[GraphVectorCandidate]:
        if not await self._client.collection_exists(self._collection_name):
            return []

        query_filter = _build_query_filter(
            scope=scope,
            resource_ids=resource_ids,
            type_field="category",
            type_values=node_categories,
            metadata_filters=metadata_filters,
        )
        response = await self._client.query_points(
            collection_name=self._collection_name,
            query=list(query_vector),
            using=self._dense_vector_name,
            query_filter=query_filter,
            limit=limit,
            with_payload=[
                "projection_id",
                "node_id",
                "resource_id",
                "content_revision",
            ],
        )
        return _parse_candidates(
            response.points,
            target_type="node",
            id_key="node_id",
            branch="node_dense",
        )


# --- Edge 向量仓储（Dense + BM25） ---

class QdrantGraphEdgeVectorRepository(
    QdrantVectorRepository, GraphEdgeVectorRepository
):
    """关系同时写 Dense 与 BM25；两路只共用 point identity，不在写入时融合。"""

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
        await self._ensure_collection(
            sparse_vectors_config={
                self._sparse_vector_name: qdrant_models.SparseVectorParams(
                    modifier=qdrant_models.Modifier.IDF
                )
            }
        )
        await self._delete_revision(
            resource_id=resource_id, content_revision=content_revision
        )
        if not edges:
            return

        points = [
            _to_edge_point(
                item=item,
                dense_vector=dense_vectors[_edge_projection_id(item)],
                lexical_text=lexical_texts[_edge_projection_id(item)],
                resource_acl=resource_acl,
                dense_vector_name=self._dense_vector_name,
                sparse_vector_name=self._sparse_vector_name,
            )
            for item in edges
        ]
        await self._client.upsert(
            collection_name=self._collection_name,
            points=points,
            wait=True,
        )

    async def search_dense(
        self,
        *,
        query_vector: Sequence[float],
        scope: PermissionScope,
        resource_ids: Sequence[str] | None,
        relation_types: Sequence[str],
        metadata_filters: Sequence[GraphFilterCondition],
        limit: int,
    ) -> list[GraphVectorCandidate]:
        if not await self._client.collection_exists(self._collection_name):
            return []

        query_filter = _build_query_filter(
            scope=scope,
            resource_ids=resource_ids,
            type_field="relation_type",
            type_values=relation_types,
            metadata_filters=metadata_filters,
        )
        response = await self._client.query_points(
            collection_name=self._collection_name,
            query=list(query_vector),
            using=self._dense_vector_name,
            query_filter=query_filter,
            limit=limit,
            with_payload=[
                "projection_id",
                "edge_id",
                "resource_id",
                "content_revision",
            ],
        )
        return _parse_candidates(
            response.points,
            target_type="edge",
            id_key="edge_id",
            branch="edge_dense",
        )

    async def search_bm25(
        self,
        *,
        query: str,
        scope: PermissionScope,
        resource_ids: Sequence[str] | None,
        relation_types: Sequence[str],
        metadata_filters: Sequence[GraphFilterCondition],
        limit: int,
    ) -> list[GraphVectorCandidate]:
        if not await self._client.collection_exists(self._collection_name):
            return []

        query_filter = _build_query_filter(
            scope=scope,
            resource_ids=resource_ids,
            type_field="relation_type",
            type_values=relation_types,
            metadata_filters=metadata_filters,
        )
        response = await self._client.query_points(
            collection_name=self._collection_name,
            query=qdrant_models.Document(
                text=query,
                model="qdrant/bm25",
                options={"tokenizer": "multilingual"},
            ),
            using=self._sparse_vector_name,
            query_filter=query_filter,
            limit=limit,
            with_payload=[
                "projection_id",
                "edge_id",
                "resource_id",
                "content_revision",
            ],
        )
        return _parse_candidates(
            response.points,
            target_type="edge",
            id_key="edge_id",
            branch="edge_bm25",
        )


# --- 辅助函数：Point 和 Payload 构造 ---

def _to_node_point(
    item: GraphNodeProjection,
    dense_vector: Sequence[float],
    resource_acl: ResourceAcl,
    dense_vector_name: str,
) -> qdrant_models.PointStruct:
    projection_id = _node_projection_id(item)
    return qdrant_models.PointStruct(
        id=_point_id("node", projection_id),
        vector={dense_vector_name: list(dense_vector)},
        payload=_node_payload(item, resource_acl, projection_id),
    )


def _to_edge_point(
    item: GraphEdgeProjection,
    dense_vector: Sequence[float],
    lexical_text: str,
    resource_acl: ResourceAcl,
    dense_vector_name: str,
    sparse_vector_name: str,
) -> qdrant_models.PointStruct:
    projection_id = _edge_projection_id(item)
    return qdrant_models.PointStruct(
        id=_point_id("edge", projection_id),
        vector={
            dense_vector_name: list(dense_vector),
            sparse_vector_name: qdrant_models.Document(
                text=lexical_text,
                model="qdrant/bm25",
                options={"tokenizer": "multilingual"},
            ),
        },
        payload=_edge_payload(item, resource_acl, projection_id),
    )


def _source_payload(
    item: GraphNodeProjection | GraphEdgeProjection,
    acl: ResourceAcl,
) -> dict[str, Any]:
    """构建来源投影的公共 payload（含 ACL 展开）。"""
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


def _point_id(kind: str, projection_id: str) -> str:
    return str(uuid5(NAMESPACE_URL, f"wisepen-rag-v3:graph:{kind}:{projection_id}"))


# --- 辅助函数：查询过滤与候选解析 ---

def _build_query_filter(
    *,
    scope: PermissionScope,
    resource_ids: Sequence[str] | None,
    type_field: str | None = None,
    type_values: Sequence[str] | None = None,
    metadata_filters: Sequence[GraphFilterCondition] = (),
) -> qdrant_models.Filter:
    """组装权限、资源、类型和自定义元数据过滤器。"""
    filters: list[qdrant_models.Condition] = [permission_filter(scope)]

    if resource_ids:
        filters.append(match_any("resource_id", list(dict.fromkeys(resource_ids))))
    if type_field and type_values:
        filters.append(match_any(type_field, list(dict.fromkeys(type_values))))
    filters.extend(_metadata_conditions(metadata_filters))

    return qdrant_models.Filter(must=filters)


def _metadata_conditions(
    metadata_filters: Sequence[GraphFilterCondition],
) -> list[qdrant_models.FieldCondition]:
    """将元数据过滤条件转为 Qdrant 字段条件。"""
    conditions: list[qdrant_models.FieldCondition] = []
    for item in metadata_filters:
        key = f"filter_values.{item.field}"
        op = item.operator.value
        if op == "eq":
            conditions.append(match_value(key, item.value))
        elif op == "gte":
            conditions.append(
                qdrant_models.FieldCondition(
                    key=key, range=qdrant_models.Range(gte=item.value)
                )
            )
        elif op == "lte":
            conditions.append(
                qdrant_models.FieldCondition(
                    key=key, range=qdrant_models.Range(lte=item.value)
                )
            )
    return conditions


def _parse_candidates(
    points: Sequence[qdrant_models.ScoredPoint],
    *,
    target_type: Literal["node", "edge"],
    id_key: str,
    branch: str,
) -> list[GraphVectorCandidate]:
    """从 ScoredPoint 解析出候选列表。"""
    candidates: list[GraphVectorCandidate] = []
    for rank, point in enumerate(points, start=1):
        payload = point.payload or {}
        proj_id = payload.get("projection_id")
        target_id = payload.get(id_key)
        resource_id = payload.get("resource_id")
        content_rev = payload.get("content_revision")

        if (
            isinstance(proj_id, str)
            and proj_id
            and isinstance(target_id, str)
            and target_id
            and isinstance(resource_id, str)
            and resource_id
            and isinstance(content_rev, str)
            and content_rev
        ):
            candidates.append(
                GraphVectorCandidate(
                    projection_id=proj_id,
                    target_type=target_type,
                    target_id=target_id,
                    resource_id=resource_id,
                    content_revision=content_rev,
                    rank=rank,
                    branch=branch,
                )
            )
    return candidates
