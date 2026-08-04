from __future__ import annotations

from dataclasses import dataclass, replace

from .._utils import assign_ranks
from ..core import RankedCandidate
from ..tokenizer import RankingTokenizer


@dataclass(frozen=True, slots=True)
class MmrDiversifierConfig:
    """MMR 多样性控制配置。"""

    lambda_mult: float = 0.72  # 相关性和多样性的平衡系数
    same_group_similarity: float = 0.92  # 同组候选的最低相似度惩罚


class MmrDiversifier:
    """基于 Jaccard 相似度和同组抑制的多样性控制器。"""

    __slots__ = ("tokenizer", "config")

    def __init__(
            self,
            *,
            tokenizer: RankingTokenizer,
            config: MmrDiversifierConfig | None = None,
    ) -> None:
        self.tokenizer = tokenizer
        self.config = config or MmrDiversifierConfig()

    def diversify(
            self,
            *,
            ranked: tuple[RankedCandidate, ...],
    ) -> tuple[RankedCandidate, ...]:

        if not ranked:
            return ()

        cfg = self.config
        if not 0.0 <= cfg.lambda_mult <= 1.0:
            raise ValueError("lambda_mult must be in [0, 1].")

        # MMR 公式要求 relevance 和 similarity 都在相近尺度。
        # 这里把原始 ranked score 压到 0~1，只用于多样化选择，不改最终 score。
        max_score = max((item.score for item in ranked), default=0.0)
        min_score = min((item.score for item in ranked), default=0.0)
        if max_score > min_score:
            relevance_scores = {
                item.candidate_id: (item.score - min_score) / (max_score - min_score)
                for item in ranked
            }
        else:
            relevance = 1.0 if max_score > 0.0 else 0.0
            relevance_scores = {item.candidate_id: relevance for item in ranked}

        token_sets = {
            item.candidate_id: set(self.tokenizer.tokenize(item.candidate.text))
            for item in ranked
        }
        selected_ids: set[str] = set()
        selected_items: list[RankedCandidate] = []

        while len(selected_items) < len(ranked):
            best_item: RankedCandidate | None = None
            best_score: float | None = None
            best_penalty = 0.0

            for item in ranked:
                if item.candidate_id in selected_ids:
                    continue

                # 当前候选和“已选集合”越像，diversity_penalty 越大。
                # 第一个候选没有已选对象可比较，所以惩罚为 0。
                if not selected_items:
                    diversity_penalty = 0.0
                else:
                    max_similarity = 0.0
                    candidate_tokens = token_sets[item.candidate_id]

                    for selected in selected_items:
                        selected_tokens = token_sets[selected.candidate_id]

                        lexical_similarity = _jaccard_similarity(
                            candidate_tokens,
                            selected_tokens,
                        )

                        # 同 group_key 通常代表同文档/同来源/同父 chunk。
                        # 即使文本 token 不完全相同，也人为提高相似度，避免同组连续霸榜。
                        if (
                                item.candidate.group_key
                                and item.candidate.group_key == selected.candidate.group_key
                        ):
                            lexical_similarity = max(
                                lexical_similarity,
                                cfg.same_group_similarity,
                            )

                        if lexical_similarity > max_similarity:
                            max_similarity = lexical_similarity

                    diversity_penalty = max_similarity

                # MMR 选择公式：
                # mmr_score = lambda * relevance - (1 - lambda) * diversity_penalty
                # relevance 越高越想选；和已选结果越相似，惩罚越大。
                mmr_score = (
                        cfg.lambda_mult * relevance_scores[item.candidate_id]
                        - (1.0 - cfg.lambda_mult) * diversity_penalty
                )

                if best_score is None or mmr_score > best_score:
                    best_item = item
                    best_score = mmr_score
                    best_penalty = diversity_penalty

            if best_item is None or best_score is None:
                break

            selected_ids.add(best_item.candidate_id)
            selected_items.append(
                replace(
                    best_item,
                    rank=len(selected_items) + 1,
                    metadata={
                        **best_item.metadata,
                        "mmr_score": best_score,
                        "diversity_penalty": best_penalty,
                    },
                )
            )

        tail = [item for item in ranked if item.candidate_id not in selected_ids]
        return assign_ranks(tuple(selected_items + tail))


def _jaccard_similarity(left_tokens: set[str], right_tokens: set[str]) -> float:
    """计算 MMR 使用的 token 集合相似度。"""
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / len(left_tokens | right_tokens)
