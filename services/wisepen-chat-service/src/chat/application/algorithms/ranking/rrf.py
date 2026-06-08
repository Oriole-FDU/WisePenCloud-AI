from collections import defaultdict
from dataclasses import dataclass
from typing import Dict, List

# 倒数排名融合常数基准
RRF_K = 60


@dataclass(frozen=True, slots=True)
class RankedList:
    """
    多路召回融合（RRF）算法的输入载体
    - name: 检索渠道的标识名称（例如: "dense_vector", "bm25"）
    - ids: 这一路渠道筛选出、且已经按降序排好序的文档 ID 队列
    - weight: 该渠道的信任权重系数，默认 1.0 平权，系数越高在融合时话语权越大
    """
    name: str
    ids: List[str]
    weight: float = 1.0

    def __post_init__(self):
        if self.weight < 0:
            raise ValueError("weight must be >= 0")


@dataclass(frozen=True, slots=True)
class RrfRankedItem:
    """
    经过 RRF 多路召回融合算法重排后的最终混合榜单单项
    - id: 融合重排后的文档 ID
    - score: 经过 RRF 倒数公式加权后计算出来的标准融合总分
    - rank: 在融合总榜单中的最终绝对排名
    - sources: 记录该文档同时被哪几路渠道获取
    """
    id: str
    score: float
    rank: int
    sources: List[str]


def weighted_rrf(
        ranked_lists: List[RankedList],
        *,
        k: int = RRF_K,
) -> List[RrfRankedItem]:
    """加权倒数排名融合算法.

    用于将多路独立召回（如 BM25 关键词检索与 Dense Vector 向量检索）的结果集
    按照各自的通道权重进行无损融合评分与稳定降序排序。
    """
    scores: Dict[str, float] = defaultdict(float)
    sources: Dict[str, List[str]] = defaultdict(list)

    # 记录每个 ID 首次出现的绝对全局物理顺序，确保在 RRF 分数持平时维持稳定排序
    # dict[id, order]
    first_seen_order: Dict[str, int] = {}

    # 去重
    for ranked_list in ranked_lists:
        seen = set()
        for i, id in enumerate(ranked_list.ids):
            if id in seen:
                continue
            seen.add(id)

            # 如果第一次出现，按加入顺序顺延
            if id not in first_seen_order:
                first_seen_order[id] = len(first_seen_order)

            # RRF 核心加权公式：Score = Weight / (K + Rank + 1)
            scores[id] += ranked_list.weight / (k + i + 1)

            if ranked_list.name not in sources[id]:
                sources[id].append(ranked_list.name)

    # 核心策略：优先按 RRF 分数绝对降序排；若分数相同，严格按首次被捕获的物理先后顺序升序排
    ordered_ids = sorted(
        scores,
        key=lambda id: (-scores[id], first_seen_order[id]),
    )

    return [
        RrfRankedItem(
            id=id,
            score=scores[id],
            rank=rank,
            sources=sources[id],
        )
        for rank, id in enumerate(ordered_ids)
    ]