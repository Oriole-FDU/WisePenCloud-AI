"""图谱拓扑投影端口。"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal, Protocol

from rag.application.graph.models import (
    GraphEdge,
    GraphEdgeProjection,
    GraphNode,
    GraphNodeProjection,
)
from rag.application.retrieval.models import TraversalDirection
from rag.domain.acl import PermissionScope, ResourceAcl
from rag.domain.repositories.graph_node_vectors import (
    GraphFilterCondition,
    GraphVectorCandidate,
)


@dataclass(frozen=True, slots=True)
class GraphSourceProjection:
    """Neo4j 有界遍历返回的来源投影，仅供本次图谱检索回查。"""

    projection_id: str
    target_type: Literal["node", "edge"]
    target_id: str
    resource_id: str
    content_revision: str
    evidence_ids: list[str]
    producer_id: str | None
    node: GraphNode | None = None
    edge: GraphEdge | None = None
    source_node_name: str = ""
    target_node_name: str = ""
    graph_rank: int = 0
    hop_count: int = 0


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
