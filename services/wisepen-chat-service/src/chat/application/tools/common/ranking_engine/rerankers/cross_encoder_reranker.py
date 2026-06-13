from __future__ import annotations

import asyncio
from dataclasses import dataclass

from sentence_transformers import CrossEncoder

from ..core.models import RankedCandidate, RankQuery
from ._rerank_utils import (
    build_failure_result,
    coerce_scores,
    rerank_by_scores,
    select_candidate_text,
    select_query_text,
)


_MODEL_CACHE: dict[str, object] = {}


@dataclass(frozen=True, slots=True)
class CrossEncoderRerankerConfig:
    """CrossEncoder 重排配置。"""

    model_name_or_path: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"  # 模型名称或本地路径
    max_candidates: int = 50  # 最多重排候选数
    batch_size: int = 16  # 批大小
    normalize_scores: bool = False  # 是否归一化模型分数
    keep_original_on_failure: bool = False  # 失败时是否保留原排序
    score_metadata_key: str = "cross_encoder_score"  # metadata 分数字段
    combine_with_original_score: bool = False  # 是否融合原始分数
    original_score_weight: float = 0.2  # 原始分数权重
    model_score_weight: float = 0.8  # 模型分数权重
    reason_prefix: str = "cross_encoder"  # reason 前缀


class CrossEncoderReranker:
    """基于 sentence-transformers CrossEncoder 的异步重排器。"""

    __slots__ = ("model", "config", "name")

    def __init__(
        self,
        *,
        model: object | None = None,
        config: CrossEncoderRerankerConfig | None = None,
    ) -> None:
        self.config = config or CrossEncoderRerankerConfig()
        self.model = model or self._load_model()
        self.name = "cross_encoder_reranker"

    def _load_model(self) -> object:
        """按模型路径复用 CrossEncoder，避免重复构建大模型。"""
        model = _MODEL_CACHE.get(self.config.model_name_or_path)
        if model is None:
            model = CrossEncoder(self.config.model_name_or_path)
            _MODEL_CACHE[self.config.model_name_or_path] = model
        return model

    async def rerank(
        self,
        *,
        query: RankQuery,
        ranked: tuple[RankedCandidate, ...],
    ) -> tuple[RankedCandidate, ...]:

        if not ranked:
            return ()

        cfg = self.config
        max_candidates = min(max(cfg.max_candidates, 0), len(ranked))
        if max_candidates <= 0:
            return ranked

        query_text = select_query_text(query)
        pairs = [
            (query_text, select_candidate_text(item))
            for item in ranked[:max_candidates]
        ]

        try:
            scores = await asyncio.to_thread(self._compute_scores, pairs)
        except Exception:
            if cfg.keep_original_on_failure:
                return build_failure_result(ranked=ranked, reranker_name=self.name)
            raise

        return rerank_by_scores(
            ranked=ranked,
            scores=scores,
            max_candidates=max_candidates,
            normalize=cfg.normalize_scores,
            combine_with_original_score=cfg.combine_with_original_score,
            original_score_weight=cfg.original_score_weight,
            model_score_weight=cfg.model_score_weight,
            score_metadata_key=cfg.score_metadata_key,
            reason_prefix=cfg.reason_prefix,
            reranker_name=self.name,
        )

    def _compute_scores(self, pairs: list[tuple[str, str]]) -> list[float]:
        """调用模型计算 pair 分数。"""
        if hasattr(self.model, "compute_score"):
            raw_scores = self.model.compute_score(pairs, batch_size=self.config.batch_size)
        else:
            raw_scores = self.model.predict(pairs, batch_size=self.config.batch_size)
        return coerce_scores(raw_scores, len(pairs))


