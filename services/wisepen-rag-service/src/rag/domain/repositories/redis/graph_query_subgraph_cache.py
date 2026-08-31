"""图查询候选子图缓存契约。"""

from collections.abc import Awaitable, Callable, Sequence
from typing import Protocol

from rag.domain.models.acl import PermissionScope
from rag.domain.models.graph import KnowledgeRelationType, TraversalDirection
from rag.domain.repositories.neo4j.knowledge_graph_repository import GraphQuerySubgraph


class GraphQuerySubgraphCache(Protocol):
    """按查询参数缓存候选子图，不缓存 query-specific 的最终响应。"""

    async def get_or_load(
        self,
        *,
        seed_node_ids: Sequence[str],
        permission_scope: PermissionScope,
        relation_types: Sequence[KnowledgeRelationType],
        direction: TraversalDirection,
        max_depth: int,
        path_limit: int,
        mention_limit_per_node: int,
        loader: Callable[[], Awaitable[GraphQuerySubgraph]],
    ) -> GraphQuerySubgraph: ...

    async def bump_epoch(self) -> str: ...

    @property
    def canonical_path_limit(self) -> int: ...
