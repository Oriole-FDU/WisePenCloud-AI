from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, Generic, TypeVar

if TYPE_CHECKING:
    from ..retrievers.base import Candidate


PayloadT = TypeVar("PayloadT")


class Fusion(Generic[PayloadT]):
    """多路候选融合基类，按输入顺序展平并稳定去重。"""

    name = "base"

    def fuse(
        self,
        result_sets: list[list[Candidate[PayloadT]]],
    ) -> list[Candidate[PayloadT]]:
        """保留各路首次出现的候选，并重写为连续 rank。"""
        merged: dict[str, Candidate[PayloadT]] = {}
        for result_set in result_sets:
            for candidate in result_set:
                merged.setdefault(candidate.candidate_id, candidate)

        return [
            replace(candidate, rank=rank, score=0.0)
            for rank, candidate in enumerate(merged.values(), start=1)
        ]
