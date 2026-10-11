"""RAG 框架共享的文档结构与最小图事实合同。"""

from .document import Document, DocumentStructure
from .graph import GraphEdge, GraphNode

__all__ = ["Document", "DocumentStructure", "GraphEdge", "GraphNode"]
