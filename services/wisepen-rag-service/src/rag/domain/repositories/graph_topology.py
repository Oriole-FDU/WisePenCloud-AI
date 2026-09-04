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
from rag.domain.repositories.graph_node_vectors import GraphVectorCandidate
from rag.domain.repositories.metadata_filters import MetadataFilterCondition


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

    def get_fact_text(self) -> str:
        """返回确定性图元用于精排和展示的事实文本。"""
        if self.edge is not None:
            lines = [
                f"来源实体: {self.source_node_name or self.edge.source_node_id}",
                f"关系: {self.edge.relation_type}",
                f"目标实体: {self.target_node_name or self.edge.target_node_id}",
            ]
            if self.edge.description:
                lines.append(f"事实说明: {self.edge.description}")
            return "\n".join(lines)
        if self.node is not None:
            lines = [
                f"实体: {self.node.name}",
                f"类别: {self.node.category}",
            ]
            if self.node.description:
                lines.append(f"说明: {self.node.description}")
            return "\n".join(lines)
        return ""


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
        metadata_filters: Sequence[MetadataFilterCondition],
        limit: int,
    ) -> list[GraphSourceProjection]: ...
