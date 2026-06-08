from dataclasses import dataclass
from typing import List

from .enums import NeighborRelation, RetrievalChannel
from ..enums import ResourceKind


@dataclass(frozen=True, slots=True)
class RagEvidence:
    """RAG 流水线内部的完整证据表示。

    包含从多路召回、RRF 融合、重排、MMR 多样性选择各阶段的完整信息，
    是证据选择流程中的核心数据载体。
    """

    evidence_id: str
    rank: int
    user_id: str
    resource_kind: ResourceKind
    resource_id: str
    index_version: str
    chunk_id: str
    parent_chunk_id: str
    parent_chunk_index: int
    chunk_index: int
    text: str
    search_text: str
    retrieval_context: str
    neighbor_texts: List[str]
    rerank_score: float
    mmr_score: float
    diversity_penalty: float
    rrf_score: float
    matched_channels: List[RetrievalChannel]
    matched_queries: List[str]


@dataclass(frozen=True, slots=True)
class EvidenceNeighborContext:
    """证据相邻上下文。

    用于 Neighbor Context Packing，
    在最终输出中为主 evidence chunk 提供上下文窗口信息。
    """

    chunk_id: str
    resource_id: str
    resource_kind: ResourceKind
    chunk_index: int
    display_name: str
    text: str
    relation: NeighborRelation


@dataclass(frozen=True, slots=True)
class RetrieveChunkEvidence:
    """最终可引用的 RAG 证据。

    这是暴露给外部（Agent / LLM）的最终证据格式，
    包含引用标记、文本、检索上下文和相关邻居信息。
    """

    rank: int
    citation_marker: str
    chunk_id: str
    resource_id: str
    resource_kind: ResourceKind
    chunk_index: int
    display_name: str
    text: str
    retrieval_context: str
    neighbor_contexts: List[EvidenceNeighborContext]
    matched_channels: List[RetrievalChannel]
    matched_queries: List[str]
    rrf_score: float
    reranker_score: float
