"""节点图谱向量投影端口。"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal, Protocol

from rag.domain.repositories.metadata_filters import (
    MetadataFilterCondition,
)

from rag.application.graph.models import (
    GraphNodeProjection,
)
from rag.domain.acl import PermissionScope, ResourceAcl


@dataclass(frozen=True, slots=True)
class GraphVectorCandidate:
    """图谱 Qdrant 初检返回的来源投影引用，不是图谱检索结果。"""

    projection_id: str
    target_type: Literal["node", "edge"]
    target_id: str
    resource_id: str
    content_revision: str
    rank: int
    branch: str


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

    async def delete_resources(self, resource_ids: Sequence[str]) -> None: ...

    async def search_dense(
        self,
        *,
        query_vector: Sequence[float],
        scope: PermissionScope,
        resource_ids: Sequence[str] | None,
        node_categories: Sequence[str],
    metadata_filters: Sequence[MetadataFilterCondition],
        limit: int,
    ) -> list[GraphVectorCandidate]: ...
