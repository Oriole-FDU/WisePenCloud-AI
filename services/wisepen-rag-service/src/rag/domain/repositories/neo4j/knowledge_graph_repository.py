"""知识图谱发布、反查和遍历的统一仓储契约。"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Protocol

from rag.domain.models.acl import PermissionScope
from rag.domain.models.graph import (
    GraphEvidence,
    KnowledgeGraph,
    KnowledgeMention,
    KnowledgeNode,
    KnowledgeRelationType,
    TraversalDirection,
)
from common.utils.document import SourceSpan


class KnowledgeGraphRevisionSupersededError(RuntimeError):
    """写入任务对应的内容版本已被更新版本取代。"""


@dataclass(slots=True)
class GraphSeedBlock:
    """LOCATE 提升出的 ReadingBlock 及其检索排序提示。"""

    resource_id: str
    content_revision: str
    reading_block_id: str
    rank: int
    # 命中区间只影响块内 mention 排序，不限制 seed 候选范围。
    matched_source_spans: list[SourceSpan] = field(default_factory=list)


@dataclass(slots=True)
class TraversedEdge:
    """图查询返回的边，并保留 EXPAND 回源所需的证据身份。"""

    edge_id: str
    source_node_id: str
    target_node_id: str
    relation_type: KnowledgeRelationType
    evidence: list[GraphEvidence] = field(default_factory=list)
    predicate: str | None = None


@dataclass(slots=True)
class TraversedPath:
    """Neo4j 有界遍历返回的节点和边序列。"""

    nodes: list[KnowledgeNode] = field(default_factory=list)
    edges: list[TraversedEdge] = field(default_factory=list)


@dataclass(slots=True)
class GraphQuerySubgraph:
    """一次有界图查询的候选路径和批量节点提及。"""

    paths: list[TraversedPath] = field(default_factory=list)
    mentions: list[KnowledgeMention] = field(default_factory=list)
    seed_node_ids: list[str] = field(default_factory=list)
    relation_types: list[KnowledgeRelationType] = field(default_factory=list)
    direction: TraversalDirection = TraversalDirection.BOTH
    max_depth: int = 1
    path_limit: int = 0
    mention_limit_per_node: int = 0
    graph_epoch: str = "0"
    cache_schema_version: str = "graph-query-subgraph:v1"


class KnowledgeGraphRepository(Protocol):
    """管理知识图谱发布生命周期并查询当前已发布图。"""

    async def initialize(self) -> None: ...

    async def begin_build(
        self,
        *,
        resource_id: str,
        content_revision: str,
        document_version: int,
    ) -> None: ...

    async def publish(
        self,
        *,
        graph: KnowledgeGraph,
        document_version: int,
    ) -> None: ...

    async def skip(
        self,
        *,
        resource_id: str,
        content_revision: str,
        document_version: int,
    ) -> None: ...

    async def delete_resources(self, resource_ids: Sequence[str]) -> None: ...

    async def find_nodes(
        self,
        *,
        reading_blocks: Sequence[GraphSeedBlock],
        permission_scope: PermissionScope,
        limit: int,
    ) -> list[KnowledgeNode]: ...

    async def find_subgraph(
        self,
        *,
        seed_node_ids: Sequence[str],
        permission_scope: PermissionScope,
        relation_types: Sequence[KnowledgeRelationType] = (),
        direction: TraversalDirection = TraversalDirection.BOTH,
        max_depth: int = 1,
        path_limit: int = 40,
        mention_limit_per_node: int = 3,
    ) -> GraphQuerySubgraph: ...
