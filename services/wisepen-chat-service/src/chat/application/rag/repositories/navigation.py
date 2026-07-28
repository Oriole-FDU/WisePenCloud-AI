from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from chat.application.rag.knowledge_navigation import (
        KnowledgeGraphExpandRequest,
        KnowledgeMentionSource,
        KnowledgeNavigationNode,
        KnowledgeNavigationPath,
        KnowledgeNavigationState,
    )
    from chat.application.rag.retrieval.models import RagPermissionScope


class KnowledgeGraphExtractionCache(Protocol):
    """知识抽取 SDK 候选图的 KV 缓存，用于跨窗口复用 SDK 原始输出。"""

    async def get_many(self, keys: Sequence[str]) -> Mapping[str, str]:
        """批量读取缓存项；未命中条目不会出现在返回结果中。"""
        ...

    async def set_many(self, values: Mapping[str, str]) -> None:
        """批量写入缓存项；调用方应保证幂等。"""
        ...


class KnowledgeGraphNavigationRepository(Protocol):
    """知识图谱导航只读访问接口，封装 Cypher/索引层实现细节。"""

    async def resolve_mentions(
        self,
        *,
        sources: tuple[KnowledgeMentionSource, ...],
        permission_scope: RagPermissionScope,
        limit: int = 32,
    ) -> tuple[KnowledgeNavigationNode, ...]:
        """根据 RAG 命中来源（resource_id + chunk_id）反查对应知识图谱节点，并完成 ACL 过滤。"""
        ...

    async def expand(
        self, request: KnowledgeGraphExpandRequest
    ) -> tuple[KnowledgeNavigationPath, ...]:
        """从 seed 节点出发，按指定方向和关系集合在 ACL 范围内扩展图路径。"""
        ...


class KnowledgeNavigationStateRepository(Protocol):
    """知识导航会话状态的持久化接口，用于跨 expand 调用保留上下文。"""

    async def create(
        self,
        *,
        user_id: str,
        session_id: str,
        root_query: str,
        known_node_ids: tuple[str, ...] = (),
    ) -> KnowledgeNavigationState:
        """创建一次新的导航会话，并初始化已发现节点集合。"""
        ...

    async def get(self, state_id: str) -> KnowledgeNavigationState | None:
        """读取导航会话；不存在时返回 None。"""
        ...

    async def add_known_nodes(
        self,
        *,
        state_id: str,
        node_ids: tuple[str, ...],
    ) -> bool:
        """向已知节点集合追加新节点；状态已删除或过期时返回 False。"""
        ...
