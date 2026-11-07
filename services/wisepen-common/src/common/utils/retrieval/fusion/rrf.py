from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, TypeVar

from .base import Fusion

if TYPE_CHECKING:
    from ..retrievers.base import Candidate


PayloadT = TypeVar("PayloadT")


class RrfFusion(Fusion[PayloadT]):
    """使用标准倒数排名分数"""

    name = "rrf"

    def __init__(self, *, k: int = 60) -> None:
        if k < 0:  # 校验 k 非负
            raise ValueError("RRF k must be non-negative")
        self.k = k

    def fuse(
        self,
        result_sets: list[list[Candidate[PayloadT]]],
    ) -> list[Candidate[PayloadT]]:
        candidates: dict[str, Candidate[PayloadT]] = {}  # 候选 ID -> 候选对象
        scores: dict[str, float] = {}  # 候选 ID -> RRF 累加分数

        for result_set in result_sets:
            seen: set[str] = set()  # 当前结果集内已处理的候选 ID
            for position, candidate in enumerate(result_set, start=1):
                cid = candidate.candidate_id
                if cid in seen:  # 同一结果集内去重
                    continue
                seen.add(cid)

                rank = candidate.rank or position  # 优先使用候选自身排名
                # 累加 RRF 分数：1 / (k + rank)
                rrf_score = 1.0 / (self.k + rank)

                if cid not in candidates:
                    candidates[cid] = candidate  # 记录候选对象
                    scores[cid] = rrf_score
                else:
                    scores[cid] += rrf_score

        # 按分数降序、首次出现顺序升序排序
        ordered = sorted(
            candidates.values(),
            key=lambda c: scores[c.candidate_id],
            reverse=True,
        )

        # 重写 rank 和 score 并返回
        return [
            replace(candidate, rank=rank, score=scores[candidate.candidate_id])
            for rank, candidate in enumerate(ordered, start=1)
        ]
