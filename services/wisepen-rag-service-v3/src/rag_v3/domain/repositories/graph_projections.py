"""图谱外部投影的写入端口；Mongo 图谱事实仍是唯一来源。"""

from collections.abc import Mapping, Sequence
from typing import Protocol

from rag_v3.domain.acl import PermissionScope, ResourceAcl
from rag_v3.domain.graph import (
    GraphEdgeProjection,
    GraphFilterCondition,
    GraphNodeProjection,
    GraphSourceProjection,
    GraphVectorCandidate,
    TraversalDirection,
)


class GraphTopologyRepository(Protocol):
    """管理 Neo4j 的逻辑拓扑和按资源 revision 区分的来源投影。"""

    async def replace_revision(
        self,
        *,
        resource_id: str,
        content_revision: str,
        nodes: Sequence[GraphNodeProjection],
        edges: Sequence[GraphEdgeProjection],
        resource_acl: ResourceAcl,
    ) -> None: ...

    async def delete_resources(self, resource_ids: Sequence[str]) -> None: ...

    async def traverse(
        self,
        *,
        candidates: Sequence[GraphVectorCandidate],
        seed_node_ids: Sequence[str],
        scope: PermissionScope,
        resource_ids: Sequence[str] | None,
        relation_types: Sequence[str],
        direction: TraversalDirection,
        max_depth: int,
        metadata_filters: Sequence[GraphFilterCondition],
        limit: int,
    ) -> list[GraphSourceProjection]: ...


class GraphNodeVectorRepository(Protocol):
    """管理节点 Dense 图谱索引，不负责查询或分数融合。"""

    async def replace_revision(
        self,
        *,
        resource_id: str,
        content_revision: str,
        nodes: Sequence[GraphNodeProjection],
        dense_vectors: Mapping[str, Sequence[float]],
        resource_acl: ResourceAcl,
    ) -> None: ...

    async def is_complete(
        self,
        *,
        resource_id: str,
        content_revision: str,
        projection_ids: Sequence[str],
    ) -> bool: ...

    async def delete_resources(self, resource_ids: Sequence[str]) -> None: ...

    async def search_dense(
        self,
        *,
        query_vector: Sequence[float],
        scope: PermissionScope,
        resource_ids: Sequence[str] | None,
        node_categories: Sequence[str],
        metadata_filters: Sequence[GraphFilterCondition],
        limit: int,
    ) -> list[GraphVectorCandidate]: ...


class GraphEdgeVectorRepository(Protocol):
    """管理关系 Dense/BM25 图谱索引，不负责查询或分数融合。"""

    async def replace_revision(
        self,
        *,
        resource_id: str,
        content_revision: str,
        edges: Sequence[GraphEdgeProjection],
        dense_vectors: Mapping[str, Sequence[float]],
        lexical_texts: Mapping[str, str],
        resource_acl: ResourceAcl,
    ) -> None: ...

    async def is_complete(
        self,
        *,
        resource_id: str,
        content_revision: str,
        projection_ids: Sequence[str],
    ) -> bool: ...

    async def delete_resources(self, resource_ids: Sequence[str]) -> None: ...

    async def search_dense(
        self,
        *,
        query_vector: Sequence[float],
        scope: PermissionScope,
        resource_ids: Sequence[str] | None,
        relation_types: Sequence[str],
        metadata_filters: Sequence[GraphFilterCondition],
        limit: int,
    ) -> list[GraphVectorCandidate]: ...

    async def search_bm25(
        self,
        *,
        query: str,
        scope: PermissionScope,
        resource_ids: Sequence[str] | None,
        relation_types: Sequence[str],
        metadata_filters: Sequence[GraphFilterCondition],
        limit: int,
    ) -> list[GraphVectorCandidate]: ...
