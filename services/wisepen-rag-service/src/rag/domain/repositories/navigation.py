from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from rag.application.rag.knowledge_navigation import (
        KnowledgeGraphCypherRequest,
        KnowledgeMentionSource,
        KnowledgeNavigationNode,
        KnowledgeNavigationPath,
        KnowledgeNavigationState,
    )
    from rag.application.rag.retrieval.models import RagPermissionScope


class KnowledgeGraphExtractionRepository(ABC):
    """知识抽取 SDK 候选图的资源内持久派生结果仓储。"""

    @abstractmethod
    async def get_many(self, keys: Sequence[str]) -> Mapping[str, str]:
        """批量读取派生项；未命中条目不会出现在返回结果中。"""
        pass

    @abstractmethod
    async def set_many(
        self,
        *,
        resource_id: str,
        values: Mapping[str, str],
    ) -> None:
        """批量写入派生项；调用方应保证幂等。"""
        pass


class KnowledgeGraphNavigationRepository(ABC):
    """知识图谱导航只读访问接口，封装 Cypher/索引层实现细节。"""

    @abstractmethod
    async def resolve_mentions(
        self,
        *,
        sources: tuple[KnowledgeMentionSource, ...],
        permission_scope: RagPermissionScope,
        limit: int = 32,
    ) -> tuple[KnowledgeNavigationNode, ...]:
        """根据 RAG 命中来源反查知识图谱节点，并完成 ACL 过滤。"""
        pass

    @abstractmethod
    async def cypher(
        self,
        request: KnowledgeGraphCypherRequest,
    ) -> tuple[KnowledgeNavigationPath, ...]:
        """从 seed 节点出发，在 ACL 范围内执行有界图遍历。"""
        pass


class KnowledgeNavigationStateRepository(ABC):
    """知识导航会话状态的持久化接口，用于跨 cypher 调用保留上下文。"""

    @abstractmethod
    async def create(
        self,
        *,
        user_id: str,
        session_id: str,
        root_query: str,
        known_graph_node_ids: tuple[str, ...],
        known_sections: Mapping[str, str],
    ) -> KnowledgeNavigationState:
        """创建一次新的导航会话，并初始化已发现节点集合。"""
        pass

    @abstractmethod
    async def get(self, state_id: str) -> KnowledgeNavigationState | None:
        """读取导航会话；不存在时返回 None。"""
        pass

    @abstractmethod
    async def add_known_graph_nodes(
        self,
        *,
        state_id: str,
        node_ids: tuple[str, ...],
    ) -> bool:
        """向图节点白名单追加新节点；状态已删除或过期时返回 False。"""
        pass

    @abstractmethod
    async def add_known_sections(
        self,
        *,
        state_id: str,
        sections: Mapping[str, str],
    ) -> bool:
        """追加 Section 及其可信资源归属；状态已删除或过期时返回 False。"""
        pass
