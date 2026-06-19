from __future__ import annotations

from collections.abc import Mapping

from chat.application.utils.ranking_engine.models import RankedCandidate


def jaccard_similarity(left_tokens: set[str], right_tokens: set[str]) -> float:
    """计算 token set Jaccard 相似度。"""
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / len(left_tokens | right_tokens)


def _format_reason(reason: str, suffix: str | None) -> str:
    """有后缀则追加，无后缀保持原样。"""
    if suffix is None:
        return reason
    return f"{reason} | {suffix}" if reason else suffix


def assign_ranks(
    items: tuple[RankedCandidate, ...],
    *,
    metadata_by_candidate_id: Mapping[str, dict[str, object]] | None = None,
    reason_suffix: str | None = None,
) -> tuple[RankedCandidate, ...]:
    """重新分配连续 rank，并可按 candidate_id 追加 metadata/reason。"""
    metadata_by_candidate_id = metadata_by_candidate_id or {}

    return tuple(
        RankedCandidate(
            candidate=item.candidate,
            rank=index,
            score=item.score,
            signals=item.signals,
            reason=_format_reason(item.reason, reason_suffix),
            metadata={
                **item.metadata,
                **metadata_by_candidate_id.get(item.candidate_id, {}),
            },
        )
        for index, item in enumerate(items, 1)
    )
