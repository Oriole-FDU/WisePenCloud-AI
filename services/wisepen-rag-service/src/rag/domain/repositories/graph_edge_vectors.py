"""关系图谱向量投影端口。"""

from collections.abc import Mapping, Sequence
from typing import Protocol

from rag.application.graph.models import (
    GraphEdgeProjection,
)
from rag.domain.acl import PermissionScope, ResourceAcl
from rag.domain.repositories.graph_node_vectors import GraphVectorCandidate
from rag.domain.repositories.metadata_filters import MetadataFilterCondition


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

    async def delete_resources(self, resource_ids: Sequence[str]) -> None: ...

    async def search_dense(
        self,
        *,
        query_vector: Sequence[float],
        scope: PermissionScope,
        resource_ids: Sequence[str] | None,
        relation_types: Sequence[str],
        metadata_filters: Sequence[MetadataFilterCondition],
        limit: int,
    ) -> list[GraphVectorCandidate]: ...

    async def search_bm25(
        self,
        *,
        query: str,
        scope: PermissionScope,
        resource_ids: Sequence[str] | None,
        relation_types: Sequence[str],
        metadata_filters: Sequence[MetadataFilterCondition],
        limit: int,
    ) -> list[GraphVectorCandidate]: ...
