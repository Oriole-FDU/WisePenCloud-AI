"""混合检索的请求、初检引用和返回模型。"""

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Literal

from common.utils.document import SourceSpan
from common.utils.ranking import RankDecision
from pydantic import BaseModel

# --- 枚举定义 ---

class GraphSearchLevel(StrEnum):
    LOW = "low"
    HIGH = "high"
    HYBRID = "hybrid"


class TraversalDirection(StrEnum):
    IN = "in"
    OUT = "out"
    BOTH = "both"


# --- 图谱检索请求与响应 ---

@dataclass(frozen=True, slots=True)
class GraphSearchRequest:
    """图谱检索能力参数，不承担 API 层的输入 schema 限制。

    `vector_top_n` 是每个向量分支的召回上限；`candidate_limit` 是并集池
    和有限遍历送入精排的上限；`top_k` 只决定最终返回多少项。
    """

    query: str
    level: GraphSearchLevel = GraphSearchLevel.HYBRID
    seed_node_ids: list[str] = field(default_factory=list)
    resource_ids: list[str] | None = None
    node_categories: list[str] = field(default_factory=list)
    relation_types: list[str] = field(default_factory=list)
    direction: TraversalDirection = TraversalDirection.BOTH
    max_depth: int = 1
    vector_top_n: int = 20  # 每个节点/关系向量分支各取多少条
    candidate_limit: int = 50  # 并集池和图遍历最多送入精排多少条
    top_k: int = 5  # 最终对外返回多少条
    plugin_id: str | None = None
    metadata_filter: BaseModel | None = None


@dataclass(frozen=True, slots=True)
class ChunkGraphHit:
    chunk_id: str
    resource_id: str
    content_revision: str
    section_id: str | None
    section_path: list[str]
    graph_ids: list[str]
    rerank_score: float


@dataclass(frozen=True, slots=True)
class DeterministicGraphFactHit:
    target_type: Literal["node", "edge"]
    target_id: str
    resource_id: str
    content_revision: str
    producer_id: str
    rerank_score: float


GraphSearchHit = ChunkGraphHit | DeterministicGraphFactHit


@dataclass(frozen=True, slots=True)
class GraphSearchResult:
    hits: list[GraphSearchHit]
    relevance_decision: str | None = None


# --- 混合检索相关结构 ---

@dataclass(frozen=True, slots=True)
class ChunkHit:
    """通过版本、ACL 与相关性门控后的检索原子。"""

    chunk_id: str
    resource_id: str
    content_revision: str
    section_id: str | None
    section_path: list[str]
    rerank_score: float
    node_ids: list[str]


@dataclass(frozen=True, slots=True)
class DynamicParent:
    """查询时由权威 Markdown 重建的单一 Section 连续阅读区间。"""

    parent_id: str
    resource_id: str
    content_revision: str
    # 父块按单个 section 分组；无标题文档的 chunk 才为 None。
    section_id: str | None
    text: str
    source_spans: list[SourceSpan]  # Python 字符半开区间。
    matched_chunk_ids: list[str]
    score: float


@dataclass(frozen=True, slots=True)
class HybridRetrievalResult:
    """混合检索结果；实体节点始终随具体命中 Chunk 返回。"""

    hits: list[ChunkHit]
    parents: list[DynamicParent]
    relevance_decision: RankDecision
