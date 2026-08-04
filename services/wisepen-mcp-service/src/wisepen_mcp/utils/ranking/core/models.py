from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

Metadata = dict[str, object]


class ScoreSignalKind(StrEnum):
    """排序信号类型，用于区分不同插件产出的信号来源。"""

    LEXICAL = "lexical"  # 词法相关性信号，例如 BM25
    FIELD = "field"  # 字段相关性信号，例如 title/body 加权
    PRIOR = "prior"  # 先验排序信号，例如原始排名
    VECTOR = "vector"  # 向量相似度信号
    RULE = "rule"  # 规则信号，例如关键词精确命中
    MODEL = "model"  # 模型重排信号，例如 cross encoder / LLM reranker
    DIVERSITY = "diversity"  # 多样性信号，例如 MMR


@dataclass(frozen=True, slots=True)
class RankQuery:
    """排序查询对象。"""

    text: str  # 查询文本
    metadata: Metadata = field(
        default_factory=dict
    )  # 调用方附加元数据，pipeline 不解释其含义


@dataclass(frozen=True, slots=True)
class RankCandidate:
    """排序 pipeline 的标准候选输入。"""

    candidate_id: str  # 候选唯一 ID
    text: str = ""  # 候选主文本，用于全文相关性、模型重排或相似度计算
    fields: dict[str, str] = field(
        default_factory=dict
    )  # 候选字段文本，例如 title、body、heading、summary
    prior_rank: int | None = None  # 外部系统给出的原始排名，只表达先验顺序或同分兜底
    group_key: str | None = None  # 多样性控制分组键，例如同文档、同来源、同域名、同父块
    metadata: Metadata = field(
        default_factory=dict
    )  # 候选附加元数据，pipeline 不解释其含义


@dataclass(frozen=True, slots=True)
class ScoreSignal:
    """单个插件产出的排序信号。"""

    candidate_id: str  # 信号所属候选 ID
    name: str  # 信号名称，例如 bm25:title、rrf、prior_rank、mmr
    value: float  # 信号分值
    kind: ScoreSignalKind = ScoreSignalKind.LEXICAL  # 信号类型
    rank: int | None = None  # 该信号内部排名，从 1 开始；无排名语义时为空
    weight: float = 1.0  # 该信号建议权重
    reason: str = ""  # 信号解释文本
    metadata: Metadata = field(
        default_factory=dict
    )  # 信号附加元数据，用于调试、解释和追踪


@dataclass(frozen=True, slots=True)
class RankedCandidate:
    """已经排序完成的对象。"""

    candidate: RankCandidate  # 原始候选对象
    rank: int  # 当前排名，从 1 开始
    score: float  # 当前综合分
    signals: tuple[ScoreSignal, ...] = ()  # 参与排序的全部信号
    reason: str = ""  # 当前排序解释
    metadata: Metadata = field(default_factory=dict)  # 当前排序阶段附加元数据

    @property
    def candidate_id(self) -> str:
        """返回候选唯一 ID。"""
        return self.candidate.candidate_id


@dataclass(frozen=True, slots=True)
class RankRequest:
    """RankingPipeline 的统一请求对象。"""

    query: RankQuery  # 排序查询
    candidates: tuple[RankCandidate, ...]  # 待排序候选集
    top_k: int  # 最终最多返回数量
    candidate_limit: int = 100  # 进入 reranker / diversifier 前的候选上限
    signals: tuple[
        ScoreSignal, ...
    ] = ()  # 调用方已经计算好的排序信号；存在时跳过 scorer


@dataclass(frozen=True, slots=True)
class RankResult:
    """RankingPipeline 的统一返回对象。"""

    ranked: tuple[RankedCandidate, ...]  # 最终排序结果
    total_candidates: int  # 输入候选总数
