"""Qdrant 向量投影后端；客户端与 collection 生命周期由外部显式管理。"""

from .vector import QdrantVectorStorage

__all__ = ["QdrantVectorStorage"]
