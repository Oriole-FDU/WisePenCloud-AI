from dataclasses import dataclass
from typing import List, Optional, TypeAlias

from chat.application.rag.permissions import RagAclProjection
from chat.application.rag.runtime.enums import RagIndexingStatus
from ..enums import ResourceKind

DenseVector: TypeAlias = List[float]


@dataclass(frozen=True, slots=True)
class RetrieveChunk:
    """检索块（Retrieve Chunk）。

    直接存储在向量数据库中用于检索的 chunk，
    包含文本内容和定位到资源所需的基本信息。
    """

    chunk_id: str
    resource_id: str
    resource_kind: ResourceKind
    chunk_index: int
    text: str
    content_hash: str


@dataclass(frozen=True, slots=True)
class SearchChunk:
    """搜索块（Search Chunk）。

    RetrieveChunk 的子块，用于精确搜索（keyword exact match）。
    一个 RetrieveChunk 可对应多个 SearchChunk，
    通过 parent_chunk_id 与父块关联。
    """

    chunk_id: str
    parent_chunk_id: str
    resource_id: str
    resource_kind: ResourceKind
    parent_chunk_index: int
    chunk_index: int
    text: str
    content_hash: str


@dataclass(frozen=True, slots=True)
class SearchChunkContext:
    """搜索块的上下文文本。

    SearchChunk 在 ES keyword search 中命中所携带的上下文信息。
    """

    chunk_id: str
    context_text: str


@dataclass(frozen=True, slots=True)
class IndexingTextPair:
    """索引文本对。

    分块后为不同检索通道准备的不同形式的文本：
    - semantic_indexing_text: 用于 dense vector 语义索引的文本
    - keyword_text: 用于 keyword (ES) 索引的文本
    """

    semantic_indexing_text: str
    keyword_text: str


@dataclass(frozen=True, slots=True)
class ChunkingResult:
    """资源分块结果。

    包含切分后产生的所有检索块和搜索块。
    """

    retrieve_chunks: List[RetrieveChunk]
    search_chunks: List[SearchChunk]


@dataclass(frozen=True, slots=True)
class VersionSnapshot:
    """Resource and pipeline version snapshot used by indexing."""

    resource_version: int
    material_hash: str
    pipeline_version: str
    index_version: str


@dataclass(frozen=True, slots=True)
class RagResource:
    """可索引的 RAG 资源。"""

    user_id: str
    resource_kind: ResourceKind
    resource_id: str
    content: str
    version: int = 1
    title: Optional[str] = None
    document_name: Optional[str] = None
    is_deleted: bool = False
    indexing_status: RagIndexingStatus = RagIndexingStatus.PENDING
    indexing_error: Optional[str] = None
    last_index_version: Optional[str] = None
    acl_projection: Optional[RagAclProjection] = None

    @property
    def display_name(self) -> str:
        if self.resource_kind == ResourceKind.NOTE and self.title:
            return self.title
        if self.resource_kind == ResourceKind.DOCUMENT and self.document_name:
            return self.document_name
        return self.resource_id


@dataclass(frozen=True, slots=True)
class ResourceUpsertResult:
    """Resource upsert result."""

    resource: RagResource
    version_snapshot: VersionSnapshot


@dataclass(frozen=True, slots=True)
class RagIndexManifest:
    """单个资源当前发布的索引清单。"""

    user_id: str
    resource_kind: ResourceKind
    resource_id: str
    resource_version: int
    material_hash: str
    pipeline_version: str
    current_index_version: str
    acl_projection: RagAclProjection
