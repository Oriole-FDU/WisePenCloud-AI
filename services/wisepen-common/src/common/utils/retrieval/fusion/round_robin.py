from __future__ import annotations

from dataclasses import replace
from itertools import zip_longest
from typing import TYPE_CHECKING, TypeVar

from .base import Fusion

if TYPE_CHECKING:
    from ..retrievers.base import Candidate


PayloadT = TypeVar("PayloadT")


class RoundRobinFusion(Fusion[PayloadT]):
    """按各路候选 rank 交替取值，并按 ID 稳定去重。"""

    name = "round_robin"

    def fuse(
        self,
        result_sets: list[list[Candidate[PayloadT]]],
    ) -> list[Candidate[PayloadT]]:
        # 各路结果集按 rank 升序排列
        ordered_sets = [
            sorted(result_set, key=lambda candidate: candidate.rank)
            for result_set in result_sets
        ]

        merged: dict[str, Candidate[PayloadT]] = {}  # 按 ID 去重，保持首次出现顺序
        # zip_longest 按位置交错遍历，自动跳过已经耗尽的结果集。
        for candidates in zip_longest(*ordered_sets):
            for candidate in candidates:
                if candidate is not None:
                    merged.setdefault(candidate.candidate_id, candidate)

        # 重写 rank 和 score 后返回
        return [
            replace(candidate, rank=rank, score=0.0)
            for rank, candidate in enumerate(merged.values(), start=1)
        ]
