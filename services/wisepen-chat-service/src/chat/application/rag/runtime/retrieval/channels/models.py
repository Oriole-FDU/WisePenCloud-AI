from dataclasses import dataclass
from typing import List, Optional

from chat.application.rag.enums import ResourceKind
from chat.application.rag.permissions import RagAclProjection
from chat.application.rag.runtime.retrieval.enums import (
    RetrievalChannel,
    RetrievalChannelStatus,
)


@dataclass(frozen=True, slots=True)
class RagIndexScope:
    """Published index scope used to filter retrieval channels."""

    user_id: str
    resource_kind: ResourceKind
    resource_id: str
    index_version: str
    acl_projection: RagAclProjection


@dataclass(frozen=True, slots=True)
class RagRetrievedCandidate:
    """Raw candidate returned by one retrieval channel."""

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
    """Retrieval result for one channel."""

    channel: RetrievalChannel
    candidates: List[RagRetrievedCandidate]


@dataclass(frozen=True, slots=True)
class ChannelRetrievalDiagnostic:
    """Execution diagnostic for one retrieval channel call."""

    channel: RetrievalChannel
    query: str
    status: RetrievalChannelStatus
    candidate_count: int
    scope_count: int
    elapsed_ms: int
    error_type: Optional[str] = None
    error_message: Optional[str] = None
