from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from rag.application.rag.acl.models import RagResourceAclProjection
    from rag.application.rag.ingestion.models import (
        RagContentProjection,
    )
    from rag.application.rag.ingestion.revision import RagProjectionStage
    from rag.application.rag.retrieval.models import (
        RagCandidateRequest,
        RagRetrievalCandidate,
    )


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


class RagCandidateRepository(ABC):
    """混合召回阶段的候选产出接口。"""

    @abstractmethod
    async def retrieve_candidates(
        self,
        request: RagCandidateRequest,
    ) -> tuple[RagRetrievalCandidate, ...]:
        """基于 dense/BM25 查询与 ACL 条件召回候选 chunk。"""
        pass
