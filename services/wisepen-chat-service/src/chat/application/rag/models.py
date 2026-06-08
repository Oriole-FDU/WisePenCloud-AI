from dataclasses import dataclass
from typing import List, Optional

from .enums import ResourceKind, RetrievalMode


@dataclass(frozen=True, slots=True)
class RagResourceUpsertCommand:
    """表示当前组件。"""
    user_id: str
    resource_kind: ResourceKind
    resource_id: str
    content: str
    title: Optional[str] = None
    document_name: Optional[str] = None


@dataclass(frozen=True, slots=True)
class RagResourceRef:
    """表示当前组件。"""
    user_id: str
    resource_kind: ResourceKind
    resource_id: str


@dataclass(frozen=True, slots=True)
class RagResourceView:
    """表示当前组件。"""
    resource_id: str
    resource_kind: ResourceKind
    version: int
    content: str
    is_deleted: bool


@dataclass(frozen=True, slots=True)
class RagResourceUpsertResult:
    """表示当前组件。"""
    resource_id: str
    resource_kind: ResourceKind
    resource_version: int
    material_hash: str
    pipeline_version: str
    index_version: str
    indexing_message_published: bool


@dataclass(frozen=True, slots=True)
class RagResourceDeleteResult:
    """表示当前组件。"""
    resource_id: str
    resource_kind: ResourceKind
    deleted: bool


@dataclass(frozen=True, slots=True)
class RagIndexManifestView:
    """表示当前组件。"""
    resource_id: str
    resource_kind: ResourceKind
    resource_version: int
    material_hash: str
    pipeline_version: str
    current_index_version: str


@dataclass(frozen=True, slots=True)
class RagIndexReadiness:
    """表示当前组件。"""
    resource_id: str
    resource_kind: ResourceKind
    target_index_version: str
    current_index_version: Optional[str]
    is_index_current: bool
    needs_indexing: bool
    can_retrieve_published_index: bool
    indexing_message_published: bool


@dataclass(frozen=True, slots=True)
class RagIndexRebuildResult:
    """表示当前组件。"""
    resource_id: str
    resource_kind: ResourceKind
    resource_version: int
    target_index_version: str
    indexing_message_published: bool


@dataclass(frozen=True, slots=True)
class RagSearchRequest:
    """表示当前组件。"""
    user_id: str
    query: str
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
    """表示当前组件。"""
    query: str
    mode: RetrievalMode
    evidence_count: int
    sufficient: bool
    insufficient_reason: Optional[str]
    included_evidence_ids: List[str]
    skipped_evidence_count: int
    assembled_context: str
    rendered_text: str
