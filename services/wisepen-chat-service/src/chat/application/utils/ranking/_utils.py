from __future__ import annotations

from dataclasses import replace

from .core import RankCandidate, RankedCandidate


def candidate_positions(candidates: tuple[RankCandidate, ...]) -> dict[str, int]:
    """校验候选 ID 唯一性，并返回稳定排序所需的原始位置。"""
    positions: dict[str, int] = {}
    for index, candidate in enumerate(candidates):
        if candidate.candidate_id in positions:
            raise ValueError(f"Duplicate candidate_id: {candidate.candidate_id}")
        positions[candidate.candidate_id] = index
    return positions


def assign_ranks(
        items: tuple[RankedCandidate, ...],
) -> tuple[RankedCandidate, ...]:
    """保留排序数据并重新分配从 1 开始的连续 rank。"""
    return tuple(replace(item, rank=index) for index, item in enumerate(items, 1))
