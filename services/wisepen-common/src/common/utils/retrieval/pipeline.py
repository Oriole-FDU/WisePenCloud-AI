from __future__ import annotations

import logging
from typing import Literal
from dataclasses import dataclass

from .diversity.base import Diversity
from .reranker.base import Reranker
from .retrievers.base import Retriever

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class RankedCandidate:
    """Pipeline 最终输出，暴露稳定身份、文本、rank、score 和透传 metadata。"""

    candidate_id: str
    text: str
    rank: int
    score: float
    metadata: dict[str, object]


class RetrievalPipeline:
    """编排一个已构造 Retriever 的精排和多样化阶段。"""

    def __init__(
        self,
        *,
        retriever: Retriever,
        reranker: Reranker | None = None, 
        diversity: Diversity | None = None, 
    ) -> None:
        self.retriever = retriever
        self.reranker = reranker if reranker is not None else Reranker()
        self.diversity = diversity if diversity is not None else Diversity()

    async def run(
        self,
        *,
        query: str,
        top_k: int,
        focus: str = "",
        diversity: Literal["off", "moderate", "high"] = "off",
    ) -> list[RankedCandidate]:
        query = query.strip()
        if not query:
            raise ValueError("query must not be empty")
        if top_k <= 0:
            raise ValueError("top_k must be positive")

        candidates = await self.retriever.retrieve(query=query)
        normalized_focus = focus.strip() or query
        try:
            candidates = await self.reranker.rerank(
                focus=normalized_focus,
                candidates=candidates,
            )
        except Exception as exc:
            logger.warning("reranker failed; preserving retrieval order", exc_info=exc)

        candidates = self.diversity.diversify(
            candidates=candidates,
            mode=diversity,
        )
        return [
            RankedCandidate(
                candidate_id=candidate.candidate_id,
                text=candidate.text,
                rank=rank,
                score=candidate.score,
                metadata=candidate.metadata,
            )
            for rank, candidate in enumerate(candidates[:top_k], start=1)
        ]
