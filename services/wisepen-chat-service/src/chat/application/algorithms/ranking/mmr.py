from dataclasses import dataclass
from typing import List, Set

from .tokenizer import tokenize_for_bm25


@dataclass(frozen=True, slots=True)
class MmrCandidate:
    """MMR 候选。

    - relevance_score 应该已经归一到 0-1。
    - similarity_text 用于计算候选间文本相似度。
    - group_key 用于抑制同一父块 / 同一资源霸榜。
    """

    id: str
    relevance_score: float
    similarity_text: str
    group_key: str


@dataclass(frozen=True, slots=True)
class MmrSelectedItem:
    """MMR 选择结果。"""

    id: str
    relevance_score: float
    diversity_penalty: float
    mmr_score: float
    rank: int


def select_by_mmr(
        candidates: List[MmrCandidate],
        *,
        top_k: int,
        lambda_mult: float = 0.72,
        same_group_similarity: float = 0.92,
) -> List[MmrSelectedItem]:
    """使用 MMR（最大边际相关性）算法选择并对候选集执行多样性去重。"""

    if top_k <= 0 or not candidates:
        return []

    if not 0.0 <= lambda_mult <= 1.0:
        raise ValueError("lambda_mult must be in [0, 1].")

    token_sets = {
        c.id: set(tokenize_for_bm25(c.similarity_text))
        for c in candidates
    }

    selected: List[MmrSelectedItem] = []
    selected_ids: Set[str] = set()

    selected_candidates: List[MmrCandidate] = []

    # 核心贪心选择迭代流
    while len(selected) < top_k and len(selected_ids) < len(candidates):
        best_candidate = None
        best_score = None
        best_penalty = 0.0

        for candidate in candidates:
            if candidate.id in selected_ids:
                continue

            # 最大相似度计算（Diversity Penalty）
            if not selected_candidates:
                diversity_penalty = 0.0
            else:
                max_sim = 0.0
                cand_tokens = token_sets[candidate.id]

                for sel_candidate in selected_candidates:
                    sel_tokens = token_sets[sel_candidate.id]

                    # Jaccard 相似度计算
                    if not cand_tokens and not sel_tokens:
                        lexical_similarity = 0.0
                    else:
                        union_size = len(cand_tokens.union(sel_tokens))
                        lexical_similarity = (
                            len(cand_tokens.intersection(sel_tokens)) / union_size
                            if union_size > 0 else 0.0
                        )

                    # 如果是同一 parent_chunk/resource，强制触顶同一组的最大相似度阈值
                    if candidate.group_key == sel_candidate.group_key:
                        lexical_similarity = max(lexical_similarity, same_group_similarity)

                    if lexical_similarity > max_sim:
                        max_sim = lexical_similarity

                diversity_penalty = max_sim

            # 标准 MMR 边际效益核心决策公式
            mmr_score = (
                    lambda_mult * candidate.relevance_score
                    - (1.0 - lambda_mult) * diversity_penalty
            )

            if best_score is None or mmr_score > best_score:
                best_candidate = candidate
                best_score = mmr_score
                best_penalty = diversity_penalty

        if best_candidate is None or best_score is None:
            break

        # 归档当前轮次的最优选择
        selected_ids.add(best_candidate.id)
        selected_candidates.append(best_candidate)
        selected.append(
            MmrSelectedItem(
                id=best_candidate.id,
                relevance_score=best_candidate.relevance_score,
                diversity_penalty=best_penalty,
                mmr_score=best_score,
                rank=len(selected),
            )
        )

    return selected
