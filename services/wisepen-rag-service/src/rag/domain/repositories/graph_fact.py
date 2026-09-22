"""图谱 Mongo 事实的仓储端口和 revision 读取结果。"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Protocol

from rag.application.graph.models import (
    GraphEdgeProjection,
    GraphNodeProjection,
    GraphChunkSource,
)


@dataclass
class GraphRevisionFacts:
    """一次 Mongo revision 读取的完整事实，供外部投影重建使用。"""

    nodes: list[GraphNodeProjection] = field(default_factory=list)
    edges: list[GraphEdgeProjection] = field(default_factory=list)
    sources: list[GraphChunkSource] = field(default_factory=list)


class GraphFactRepository(Protocol):
    """按完整 revision 替换图谱事实；本阶段不提供图遍历。"""

    async def replace_revision(
        self,
        *,
        resource_id: str,
        content_revision: str,
        nodes: list[GraphNodeProjection],
        edges: list[GraphEdgeProjection],
        sources: list[GraphChunkSource],
    ) -> None: ...

    async def get_revision_facts(
        self,
        *,
        resource_id: str,
        content_revision: str,
    ) -> GraphRevisionFacts: ...

    async def get_sources(self, source_ids: Sequence[str]) -> list[GraphChunkSource]: ...

    async def get_node_projections(
        self, refs: Sequence[tuple[str, str, str]]
    ) -> list[GraphNodeProjection]: ...
