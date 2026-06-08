from dataclasses import dataclass
from typing import Dict, List, Optional

from .enums import ResourceKind, RetrievalMode
from .permissions import RagAclProjection, RagGroupRole
from .runtime.enums import RagIndexingStatus
from .runtime.retrieval.enums import (
    InsufficientReason,
    RagRecommendedNextAction,
)


@dataclass(frozen=True, slots=True)
class RagResourceUpsertCommand:
    
    user_id: str
    resource_kind: ResourceKind
    resource_id: str
    content: str
    title: Optional[str] = None
    document_name: Optional[str] = None
    acl_projection: Optional[RagAclProjection] = None


@dataclass(frozen=True, slots=True)
class RagResourceRef:
    
    user_id: str
    resource_kind: ResourceKind
    resource_id: str


@dataclass(frozen=True, slots=True)
class RagResourceView:
    
    resource_id: str
    resource_kind: ResourceKind
    version: int
    content: str
    is_deleted: bool
    indexing_status: RagIndexingStatus
    indexing_error: Optional[str]
    last_index_version: Optional[str]


@dataclass(frozen=True, slots=True)
class RagResourceUpsertResult:
    
    resource_id: str
    resource_kind: ResourceKind
    resource_version: int
    material_hash: str
    pipeline_version: str
    index_version: str
    indexing_message_published: bool


@dataclass(frozen=True, slots=True)
class RagResourceDeleteResult:
    
    resource_id: str
    resource_kind: ResourceKind
    deleted: bool


@dataclass(frozen=True, slots=True)
class RagIndexManifestView:
    
    resource_id: str
    resource_kind: ResourceKind
    resource_version: int
    material_hash: str
    pipeline_version: str
    current_index_version: str


@dataclass(frozen=True, slots=True)
class RagIndexReadiness:
    
    resource_id: str
    resource_kind: ResourceKind
    target_index_version: str
    current_index_version: Optional[str]
    indexing_status: RagIndexingStatus
    indexing_error: Optional[str]
    last_index_version: Optional[str]
    is_index_current: bool
    needs_indexing: bool
    can_retrieve_published_index: bool
    indexing_message_published: bool


@dataclass(frozen=True, slots=True)
class RagIndexRebuildResult:
    
    resource_id: str
    resource_kind: ResourceKind
    resource_version: int
    target_index_version: str
    indexing_message_published: bool


@dataclass(frozen=True, slots=True)
class RagSearchRequest:
    
    user_id: str
    query: str
    group_role_map: Optional[Dict[str, RagGroupRole]] = None
    mode: RetrievalMode = RetrievalMode.NORMAL
    resource_kinds: Optional[List[ResourceKind]] = None
    semantic_queries: Optional[List[str]] = None
    keyword_queries: Optional[List[str]] = None
    top_k: Optional[int] = None
    fusion_top_k: Optional[int] = None
    rerank_top_n: Optional[int] = None
    final_top_k: Optional[int] = None
    neighbor_before: Optional[int] = None
    neighbor_after: Optional[int] = None
    mmr_lambda: Optional[float] = None


@dataclass(frozen=True, slots=True)
class RagSearchResult:
    
    query: str
    mode: RetrievalMode
    evidence_count: int
    sufficient: bool
    insufficient_reason: Optional[InsufficientReason]
    recommended_next_action: RagRecommendedNextAction
    rewrite_guidance: Optional[str]
    included_evidence_ids: List[str]
    skipped_evidence_count: int
    assembled_context: str
    rendered_text: str
