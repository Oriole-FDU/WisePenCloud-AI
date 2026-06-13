from __future__ import annotations

from collections.abc import Iterable

from ..core.models import RankedCandidate, RankQuery


def select_query_text(query: RankQuery) -> str:
    """选择 reranker 使用的查询文本。"""
    return query.all_queries[0] if query.all_queries else ""


def select_candidate_text(item: RankedCandidate) -> str:
    """选择 reranker 使用的候选文本。"""
    if item.candidate.text.strip():
        return item.candidate.text
    return " ".join(value for value in item.candidate.fields.values() if value.strip())


def coerce_scores(raw_scores: object, expected_count: int) -> list[float]:
    """把模型返回分数统一转成 list[float]。"""
    if isinstance(raw_scores, int | float):
        scores = [float(raw_scores)]
    elif hasattr(raw_scores, "tolist"):
        value = raw_scores.tolist()
        scores = [float(value)] if isinstance(value, int | float) else [float(v) for v in value]
    elif isinstance(raw_scores, Iterable) and not isinstance(raw_scores, str | bytes):
        scores = [float(score) for score in raw_scores]
    else:
        scores = [float(raw_scores)]

    if len(scores) != expected_count:
        raise ValueError(f"Expected {expected_count} rerank scores, got {len(scores)}.")
    return scores


def normalize_scores(scores: list[float]) -> list[float]:
    """对分数做 min-max 归一化。"""
    if not scores:
        return []
    min_score = min(scores)
    max_score = max(scores)
    if max_score == min_score:
        return [0.0 for _ in scores]
    return [(score - min_score) / (max_score - min_score) for score in scores]


def append_reason(reason: str, reason_prefix: str, score: float) -> str:
    """追加 rerank 分数解释。"""
    addition = f"{reason_prefix}={score:.4f}"
    return f"{reason} | {addition}" if reason else addition


def assign_ranks(ranked: tuple[RankedCandidate, ...]) -> tuple[RankedCandidate, ...]:
    """重新分配连续 rank。"""
    return tuple(
        RankedCandidate(
            candidate=item.candidate,
            rank=index,
            score=item.score,
            signals=item.signals,
            reason=item.reason,
            metadata=item.metadata,
        )
        for index, item in enumerate(ranked, 1)
    )


def build_failure_result(
    *,
    ranked: tuple[RankedCandidate, ...],
    reranker_name: str,
) -> tuple[RankedCandidate, ...]:
    """构造失败时保留原顺序的结果。"""
    return assign_ranks(
        tuple(
            RankedCandidate(
                candidate=item.candidate,
                rank=item.rank,
                score=item.score,
                signals=item.signals,
                reason=item.reason,
                metadata={
                    **item.metadata,
                    "reranker": reranker_name,
                    "rerank_failed": True,
                },
            )
            for item in ranked
        )
    )


def rerank_by_scores(
    *,
    ranked: tuple[RankedCandidate, ...],
    scores: list[float],
    max_candidates: int,
    normalize: bool,
    combine_with_original_score: bool,
    original_score_weight: float,
    model_score_weight: float,
    score_metadata_key: str,
    reason_prefix: str,
    reranker_name: str,
) -> tuple[RankedCandidate, ...]:
    """按模型分数重排 head，并保持 tail 原相对顺序。"""
    head = ranked[:max_candidates]
    tail = ranked[max_candidates:]
    model_scores = normalize_scores(scores) if normalize else scores
    items: list[tuple[int, float, float, RankedCandidate]] = []

    for index, item in enumerate(head):
        model_score = model_scores[index]
        final_score = (
            original_score_weight * item.score + model_score_weight * model_score
            if combine_with_original_score
            else model_score
        )
        items.append((index, final_score, model_score, item))

    ordered = sorted(items, key=lambda item: (-item[1], item[0]))
    reranked_head = tuple(
        RankedCandidate(
            candidate=item.candidate,
            rank=rank,
            score=final_score,
            signals=item.signals,
            reason=append_reason(item.reason, reason_prefix, model_score),
            metadata={
                **item.metadata,
                "reranker": reranker_name,
                "original_rank": item.rank,
                "original_score": item.score,
                score_metadata_key: model_score,
            },
        )
        for rank, (_, final_score, model_score, item) in enumerate(ordered, 1)
    )

    return assign_ranks(reranked_head + tail)
