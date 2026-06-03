from dataclasses import dataclass
from typing import List, Optional

from ..enums import ResourceKind, RetrievalMode


@dataclass(frozen=True, slots=True)
class RagRetrievalQuery:
    """RAG 检索查询。

    包含用户原始查询、为不同检索通道准备的查询变体、
    检索模式和各阶段数量控制参数。
    """

    user_id: str
    query: str
    semantic_queries: List[str]
    keyword_queries: List[str]
    mode: RetrievalMode = RetrievalMode.NORMAL
    resource_kinds: Optional[List[ResourceKind]] = None
    top_k: int = 30
    fusion_top_k: int = 50
    rerank_top_n: int = 30
    final_top_k: int = 8
    neighbor_before: int = 1
    neighbor_after: int = 1
    mmr_lambda: float = 0.72


@dataclass(frozen=True, slots=True)
class RagIndexScope:
    """RAG 索引范围。

    标识某个资源在特定索引版本下的定位信息，
    用于检索时限定查询范围。
    """

    user_id: str
    resource_kind: ResourceKind
    resource_id: str
    index_version: str
