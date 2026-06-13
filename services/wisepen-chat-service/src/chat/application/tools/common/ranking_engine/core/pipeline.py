from __future__ import annotations

from dataclasses import dataclass

from .protocols import Diversifier, Fusion, Reranker, Scorer


@dataclass(frozen=True, slots=True)
class RankingPipeline:
    """排序流水线，声明所有插件的固定执行顺序。"""

    name: str  # Pipeline 名称
    fusion: Fusion  # 分数融合插件
    scorers: tuple[Scorer, ...] = ()  # 打分插件列表
    reranker: Reranker | None = None  # 二次重排插件（可选，至多一个）
    diversifier: Diversifier | None = None  # 多样性控制插件（可选，至多一个）