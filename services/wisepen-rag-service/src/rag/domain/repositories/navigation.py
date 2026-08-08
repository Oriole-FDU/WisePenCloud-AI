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
    from rag.application.rag.ingestion.models import RagSectionReadingBlock
    from rag.application.rag.retrieval.models import RagPermissionScope
    from rag.application.rag.section_navigation.models import RagSectionView


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


class RagSectionNavigationRepository(ABC):
    """标题树节点和轻量 frontier 的读取接口。"""

    @abstractmethod
    async def load_applied_section_views(
        self,
        *,
        resource_id: str,
        section_ids: Sequence[str],
    ) -> tuple[RagSectionView, ...]:
        """按请求顺序读取 Section 及其轻量 frontier。"""
        pass

    @abstractmethod
    async def load_applied_section_reading_blocks(
        self,
        *,
        resource_id: str,
        section_ids: Sequence[str],
    ) -> tuple[RagSectionReadingBlock, ...]:
        """按 Section 和块内顺序读取完整 ReadingBlock 列表。"""
        pass
