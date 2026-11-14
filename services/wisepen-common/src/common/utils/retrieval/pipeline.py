"""通用检索阶段编排；业务授权与正文准备通过显式异步回调完成。"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import replace
from typing import Generic, Literal, TypeVar, cast

from .diversity.base import Diversity
from .reranker.base import Reranker
from .retrievers.base import Candidate, Retriever

logger = logging.getLogger(__name__)
PayloadT = TypeVar("PayloadT")
PreparedT = TypeVar("PreparedT")


class RetrievalPipeline(Generic[PayloadT, PreparedT]):
    """依次召回、准备、重排与多样化；没有准备回调时两种 payload 类型相同。"""

    def __init__(
        self,
        *,
        retriever: Retriever[PayloadT],
        prepare_candidates: Callable[
            [list[Candidate[PayloadT]]], Awaitable[list[Candidate[PreparedT]]]
        ] | None = None,
        reranker: Reranker[PreparedT] | None = None,
        diversity: Diversity[PreparedT] | None = None,
    ) -> None:
        self.retriever = retriever
        self.prepare_candidates = prepare_candidates
        self.reranker = reranker if reranker is not None else Reranker()
        self.diversity = diversity if diversity is not None else Diversity()

    async def run(
        self,
        *,
        query: str,
        top_k: int,
        focus: str = "",
        diversity: Literal["off", "moderate", "high"] = "off",
    ) -> list[Candidate[PreparedT]]:
        query = query.strip()
        if not query:
            raise ValueError("query must not be empty")
        if top_k <= 0:
            raise ValueError("top_k must be positive")
        if diversity not in {"off", "moderate", "high"}:
            raise ValueError(f"unsupported diversity mode: {diversity}")

        recalled = await self.retriever.retrieve(query=query)
        if not recalled:
            return []
        candidates = (
            await self.prepare_candidates(recalled)
            if self.prepare_candidates is not None
            else recalled
        )
        if not candidates:
            return []

        # 准备异常直接传播；只有重排是允许降级的附加阶段。
        try:
            candidates = await self.reranker.rerank(
                focus=focus.strip() or query,
                candidates=candidates,
            )
        except Exception as exc:
            logger.warning("reranker failed; preserving retrieval order", exc_info=exc)

        candidates = self.diversity.diversify(candidates=candidates, mode=diversity)
        return [
            replace(candidate, rank=rank)
            for rank, candidate in enumerate(candidates[:top_k], start=1)
        ]
