from __future__ import annotations

from chat.application.rag.evidence import RagEvidenceMaterializer
from chat.application.rag.section_navigation import RagLocatedSection, RagSectionNavigator

from .models import RagRetrievalRequest
from .retriever import RagCandidateRetriever


class RagKnowledgeLocator:
    """编排召回、证据回源和 SectionView 提升。"""

    __slots__ = ("_materializer", "_retriever", "_section_navigator")

    def __init__(
        self,
        *,
        retriever: RagCandidateRetriever,
        materializer: RagEvidenceMaterializer,
        section_navigator: RagSectionNavigator,
    ) -> None:
        self._retriever = retriever
        self._materializer = materializer
        self._section_navigator = section_navigator

    async def locate(self, request: RagRetrievalRequest) -> tuple[RagLocatedSection, ...]:
        """执行召回、证据回源和上下文构建。"""
        ranked_hits = await self._retriever.retrieve(request)

        materialized_hits = await self._materializer.materialize(
            hits=ranked_hits, 
            scope=request.permission_scope,
        )

        return await self._section_navigator.build_hits(materialized_hits)
