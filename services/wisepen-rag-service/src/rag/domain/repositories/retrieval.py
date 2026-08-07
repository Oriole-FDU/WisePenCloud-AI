from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from rag.application.rag.acl.models import RagResourceAclProjection
    from rag.application.rag.evidence.models import RagMaterializedSource
    from rag.application.rag.ingestion.models import (
        RagContentProjection,
        RagSectionReadingBlock,
    )
    from rag.application.rag.ingestion.revision import RagProjectionStage
    from rag.application.rag.resource_snapshot import (
        RagResourceContentReadResult,
        RagResourceSnapshot,
    )
    from rag.application.rag.retrieval.models import (
        RagCandidateRequest,
        RagRetrievalCandidate,
    )
    from rag.application.rag.section_navigation.models import RagSectionView


class RagVectorIndexRepository(ABC):
    """Qdrant dense 与原生 BM25 混合索引的写入和向量复用接口。"""

    @abstractmethod
    async def load_reusable_vectors(
        self,
        projection: RagContentProjection,
    ) -> Mapping[str, Sequence[float]]:
        """基于 embedding profile 和 index_text 读取可复用向量。"""
        pass

    @abstractmethod
    async def upsert_staged_projection(
        self,
        *,
        projection: RagContentProjection,
        stage: RagProjectionStage,
        dense_vectors: Mapping[str, Sequence[float]],
        acl_projection: RagResourceAclProjection | None,
    ) -> None:
        """写入 staging 向量与 ACL 标签。"""
        pass

    @abstractmethod
    async def delete_other_revisions(
        self,
        *,
        resource_id: str,
        keep_content_revision: str,
    ) -> None:
        """删除指定资源除 keep_content_revision 之外的向量。"""
        pass


class RagContextIndexingRepository(ABC):
    """chunk 上下文补全结果的资源内持久派生文本仓储。"""

    @abstractmethod
    async def get_many(
        self,
        *,
        resource_id: str,
        keys: Sequence[str],
    ) -> Mapping[str, str]:
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


class RagCandidateRepository(ABC):
    """混合召回阶段的候选产出接口。"""

    @abstractmethod
    async def retrieve_candidates(
        self,
        request: RagCandidateRequest,
    ) -> tuple[RagRetrievalCandidate, ...]:
        """基于 dense/BM25 查询与 ACL 条件召回候选 chunk。"""
        pass


class RagSourceRepository(ABC):
    """Applied SourceRef 的权威回源接口。"""

    @abstractmethod
    async def load_applied_sources(
        self,
        *,
        resource_id: str,
        ref_ids: Sequence[str],
    ) -> tuple[RagMaterializedSource, ...]:
        """读取已应用的 SourceRef 原文。"""
        pass

    @abstractmethod
    async def load_applied_reading_blocks(
        self,
        *,
        resource_id: str,
        reading_block_ids: Sequence[str],
    ) -> tuple[RagSectionReadingBlock, ...]:
        """按检索子块引用读取当前 applied Section 阅读块。"""
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


class RagResourceSnapshotRepository(ABC):
    """资源副本的文档结构与按 page/section 读取接口。"""

    @abstractmethod
    async def load_applied_resource_snapshot(
        self,
        *,
        resource_id: str,
    ) -> RagResourceSnapshot | None:
        """读取当前 applied revision 的文档结构。"""
        pass

    @abstractmethod
    async def read_applied_page_content(
        self,
        *,
        resource_id: str,
        page_labels: Sequence[str],
    ) -> RagResourceContentReadResult | None:
        """按页标签批量读取当前 applied revision 的正文窗口。"""
        pass

    @abstractmethod
    async def read_applied_section_content(
        self,
        *,
        resource_id: str,
        section_ids: Sequence[str],
    ) -> RagResourceContentReadResult | None:
        """按 Section ID 批量读取当前 applied ReadingBlock。"""
        pass
