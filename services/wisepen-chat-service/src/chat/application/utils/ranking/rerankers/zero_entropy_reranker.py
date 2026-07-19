from __future__ import annotations

from dataclasses import dataclass, replace

from zeroentropy import APIError, AsyncZeroEntropy

from .._utils import assign_ranks
from ..core import RankedCandidate, RankQuery


class ZeroEntropyRerankerError(RuntimeError):
    """ZeroEntropy 重排调用失败。"""


@dataclass(frozen=True, slots=True)
class ZeroEntropyRerankerConfig:
    """ZeroEntropy 重排配置。"""

    model: str  # ZeroEntropy rerank 模型名
    top_n: int | None = None  # None 表示使用当前 ranked 全量


class ZeroEntropyReranker:
    """基于 ZeroEntropy rerank API 的异步重排器。"""

    __slots__ = ("client", "config")

    def __init__(
            self,
            *,
            client: AsyncZeroEntropy,
            config: ZeroEntropyRerankerConfig,
    ) -> None:
        self.client = client
        self.config = config

    async def rerank(
            self,
            *,
            query: RankQuery,
            ranked: tuple[RankedCandidate, ...],
    ) -> tuple[RankedCandidate, ...]:

        if not ranked:
            return ()

        cfg = self.config
        top_n = (
            len(ranked) if cfg.top_n is None else min(max(cfg.top_n, 0), len(ranked))
        )
        if top_n <= 0:
            return ()

        documents = [item.candidate.text for item in ranked]

        try:
            response = await self.client.models.rerank(
                model=cfg.model,
                query=query.text,
                documents=documents,
                top_n=top_n,
            )
        except APIError as exc:
            raise ZeroEntropyRerankerError(f"ZeroEntropy rerank failed: {exc}") from exc

        reranked: list[RankedCandidate] = []
        selected_indexes: set[int] = set()

        for item in response.results:
            index = item.index
            score = float(item.relevance_score)

            if index < 0 or index >= len(ranked):
                raise ZeroEntropyRerankerError(
                    "ZeroEntropy rerank result.index is out of range."
                )
            if not 0.0 <= score <= 1.0:
                raise ZeroEntropyRerankerError(
                    "ZeroEntropy rerank relevance_score must be in [0, 1]."
                )
            if index in selected_indexes:
                continue

            selected_indexes.add(index)
            source = ranked[index]
            reranked.append(
                replace(
                    source,
                    rank=0,
                    score=score,
                    metadata={
                        **source.metadata,
                        "rerank_score": score,
                        "rerank_model": cfg.model,
                    },
                )
            )

        reranked.extend(
            source
            for index, source in enumerate(ranked)
            if index not in selected_indexes
        )
        return assign_ranks(tuple(reranked))
