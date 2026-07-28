from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from chat.application.rag.acl.models import RagResourceAclProjection
    from chat.application.rag.evidence.models import RagMaterializedSource
    from chat.application.rag.ingestion.models import RagContentProjection
    from chat.application.rag.ingestion.models import RagSectionReadingBlock
    from chat.application.rag.ingestion.revision import RagProjectionStage
    from chat.application.rag.retrieval.models import (
        RagCandidateRequest,
        RagRetrievalCandidate,
    )
    from chat.application.rag.section_navigation.models import RagSectionView


class RagVectorIndexRepository(Protocol):
    """Qdrant dense + native BM25 混合索引的写入与向量复用接口。"""

    async def load_reusable_vectors(
        self, projection: RagContentProjection
    ) -> Mapping[str, Sequence[float]]:
        """基于 embedding profile 和 index_text 读取可复用向量。"""
        ...

    async def upsert_staged_projection(
        self,
        *,
        projection: RagContentProjection,
        stage: RagProjectionStage,
        dense_vectors: Mapping[str, Sequence[float]],
        acl_projection: RagResourceAclProjection | None,
    ) -> None:
        """写入 staging 向量与 ACL 标签；applied 之前不可被检索侧消费。"""
        ...

    async def delete_other_revisions(
        self,
        *,
        resource_id: str,
        keep_content_revision: str,
    ) -> None:
        """删除指定资源除 keep_content_revision 之外的全部向量。"""
        ...


class RagContextIndexingCache(Protocol):
    """chunk 上下文补全结果的 KV 缓存接口（key -> rendered context 字符串）。"""

    async def get_many(self, keys: Sequence[str]) -> Mapping[str, str]:
        """批量读取缓存项；未命中条目不会出现在返回结果中。"""
        ...

    async def set_many(self, values: Mapping[str, str]) -> None:
        """批量写入缓存项；调用方应保证幂等。"""
        ...


class RagCandidateRepository(Protocol):
    """混合召回阶段的候选产出接口。"""

    async def retrieve_candidates(
        self, request: RagCandidateRequest
    ) -> tuple[RagRetrievalCandidate, ...]:
        """基于 dense/BM25 查询与 ACL 过滤条件召回候选 chunk。"""
        ...


class RagSourceRepository(Protocol):
    """Applied SourceRef 的权威回源接口，用于证据落地。"""

    async def load_applied_sources(
        self,
        *,
        resource_id: str,
        ref_ids: Sequence[str],
    ) -> tuple[RagMaterializedSource, ...]:
        """读取已应用的 SourceRef 原文；缺失条目表示 evidence 不可用。"""
        ...

    async def load_applied_reading_blocks(
        self,
        *,
        resource_id: str,
        reading_block_ids: Sequence[str],
    ) -> tuple[RagSectionReadingBlock, ...]:
        """按检索子块引用读取当前 applied revision 的 Section 阅读块。"""
        ...


class RagSectionNavigationRepository(Protocol):
    """标题树节点和轻量 frontier 的读取接口。"""

    async def load_applied_section_views(
        self,
        *,
        resource_id: str,
        section_ids: Sequence[str],
    ) -> tuple[RagSectionView, ...]:
        """按请求顺序读取 Section 及其轻量 parent/sibling/children frontier。"""
        ...

    async def load_applied_section_reading_blocks(
        self,
        *,
        resource_id: str,
        section_ids: Sequence[str],
    ) -> tuple[RagSectionReadingBlock, ...]:
        """按 Section 和块内顺序读取完整的 applied ReadingBlock 列表。"""
        ...
