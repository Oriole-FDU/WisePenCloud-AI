"""Chunk 级向量检索用例、召回器与命中结果组装。"""

from .hit import (
    ChunkHit,
    GraphNodeReference,
    PreparedChunk,
    build_chunk_hits,
)
from .retrieval import ChunkRetrieval
from .retriever import (
    ChunkRetriever,
    ChunkRetrievalMode,
)

__all__ = [
    "ChunkHit",
    "ChunkRetriever",
    "GraphNodeReference",
    "PreparedChunk",
    "ChunkRetrieval",
    "ChunkRetrievalMode",
    "build_chunk_hits",
]
