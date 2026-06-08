from dataclasses import dataclass
from typing import Dict, List, Optional

from chat.application.rag.enums import ResourceKind, RetrievalMode
from chat.application.rag.permissions import RagGroupRole
from .enums import NeighborRelation, RetrievalChannel


@dataclass(frozen=True, slots=True)
class RagRetrievalQuery:
    """RAG retrieval query."""

    user_id: str
    group_role_map: Dict[str, RagGroupRole]
    query: str
    semantic_queries: List[str]
    keyword_queries: List[str]
    mode: RetrievalMode = RetrievalMode.NORMAL
    resource_kinds: Optional[List[ResourceKind]] = None
    active_channels: Optional[List[RetrievalChannel]] = None
    channel_weights: Optional[Dict[RetrievalChannel, float]] = None
    top_k: int = 30
    fusion_top_k: int = 50
    rerank_top_n: int = 30
    final_top_k: int = 8
    neighbor_before: int = 1
    neighbor_after: int = 1
    mmr_lambda: float = 0.72


@dataclass(frozen=True, slots=True)
class EvidenceNeighborContext:
    """Neighbor chunk context attached to one evidence."""

    chunk_id: str
    resource_id: str
    resource_kind: ResourceKind
    chunk_index: int
    text: str
    relation: NeighborRelation


@dataclass(frozen=True, slots=True)
class RagEvidence:
    """Internal complete RAG evidence."""

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
    neighbor_contexts: List[EvidenceNeighborContext]
    rerank_score: float
    mmr_score: float
    diversity_penalty: float
    rrf_score: float
    matched_channels: List[RetrievalChannel]
    matched_queries: List[str]


