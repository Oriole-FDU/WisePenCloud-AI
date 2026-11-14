from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Generic, TypeVar

from ..fusion.base import Fusion

PayloadT = TypeVar("PayloadT")


@dataclass(frozen=True, slots=True)
class Candidate(Generic[PayloadT]):
    """通用排名载体；算法只改 rank/score，业务数据由 payload 原样承载。"""

    candidate_id: str
    payload: PayloadT
    rank: int
    score: float = 0.0


class Retriever(Generic[PayloadT]):
    """可插拔召回实现；业务方覆盖 retrieve 即可接入任意数据源。"""

    name = "default"

    def __init__(self, *, top_k: int = 30) -> None:
        if top_k <= 0:
            raise ValueError("retriever top_k must be positive")
        self.top_k = top_k

    async def retrieve(self, query: str) -> list[Candidate[PayloadT]]:
        return []


class FusionRetriever(Retriever[PayloadT]):
    """递归组合多个 Retriever，并把融合预算封装在检索组件内部。"""

    name = "fusion"

    def __init__(
        self,
        *,
        retrievers: list[Retriever[PayloadT]],
        fusion: Fusion[PayloadT] | None = None,
        top_k: int = 60,
    ) -> None:
        super().__init__(top_k=top_k)
        self.retrievers = retrievers
        self.fusion = fusion if fusion is not None else Fusion()

    async def retrieve(self, query: str) -> list[Candidate[PayloadT]]:
        result_sets = await asyncio.gather(
            *(retriever.retrieve(query=query) for retriever in self.retrievers)
        )
        return self.fusion.fuse(result_sets=result_sets)[: self.top_k]
