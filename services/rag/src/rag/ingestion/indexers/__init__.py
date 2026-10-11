"""加工后 Chunk 的索引写入接口。"""

from .base import Indexer
from .chunk import ChunkFactsIndexer

__all__ = ["ChunkFactsIndexer", "Indexer"]
