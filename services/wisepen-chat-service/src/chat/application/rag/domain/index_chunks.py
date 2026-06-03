from dataclasses import dataclass
from typing import List, TypeAlias

from ..enums import ResourceKind

DenseVector: TypeAlias = List[float]


@dataclass(frozen=True, slots=True)
class SparseVector:
    """稀疏向量表示。

    用于 BM25 sparse vector 检索，
    以 indices + values 的压缩格式存储稀疏向量。
    """

    indices: List[int]
    values: List[float]


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
