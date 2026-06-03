from dataclasses import dataclass
from typing import List, Optional

from .enums import RetrievalChannel
from ..enums import ResourceKind


@dataclass(frozen=True, slots=True)
class RagRetrievedCandidate:
    """检索命中的原始候选。

    从某个检索通道（如 dense_semantic / sparse_lexical / keyword_exact）
    返回的原始命中记录，包含向量数据库或搜索引擎返回的元数据。
    """

    channel: RetrievalChannel
    score: float
    user_id: str
    resource_kind: ResourceKind
    resource_id: str
    index_version: str
    chunk_id: str
    parent_chunk_id: str
    parent_chunk_index: int
    chunk_index: int
    matched_query: str


@dataclass(frozen=True, slots=True)
class ChannelRetrievalResult:
    """单个通道的检索结果。"""

    channel: RetrievalChannel
    candidates: List[RagRetrievedCandidate]


@dataclass(frozen=True, slots=True)
class SearchChunkHit:
    """搜索块命中记录。

    用于在父块聚合阶段记录父块下最佳子块的命中信息。
    """

    user_id: str
    resource_kind: ResourceKind
    index_version: str
    chunk_id: str
    parent_chunk_id: str
    resource_id: str
    channel: RetrievalChannel
    rank: int
    score: Optional[float]
    matched_query: str
