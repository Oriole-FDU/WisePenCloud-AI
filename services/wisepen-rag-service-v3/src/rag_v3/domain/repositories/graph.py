"""图谱 Mongo 事实的仓储端口。"""

from collections.abc import Sequence
from typing import Protocol

from rag_v3.domain.graph import (
    GraphEdgeProjection,
    GraphNodeProjection,
    GraphRevisionFacts,
    TextGraphEvidence,
)


class GraphFactRepository(Protocol):
    """按完整 revision 替换图谱事实；本阶段不提供图遍历。"""

    async def replace_revision(
        self,
        *,
        resource_id: str,
        content_revision: str,
        nodes: tuple[GraphNodeProjection, ...],
        edges: tuple[GraphEdgeProjection, ...],
        evidences: tuple[TextGraphEvidence, ...],
    ) -> None: ...

    async def get_revision_facts(
        self,
        *,
        resource_id: str,
        content_revision: str,
    ) -> GraphRevisionFacts: ...

    async def get_evidences(self, evidence_ids: Sequence[str]) -> list[TextGraphEvidence]: ...
