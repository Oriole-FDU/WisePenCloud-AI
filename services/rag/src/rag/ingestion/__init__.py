"""文档分块、内容加工与索引写入的串行摄入编排。"""

from .chunkers.base import Chunk
from .pipeline import IngestionPipeline

__all__ = ["Chunk", "IngestionPipeline"]
