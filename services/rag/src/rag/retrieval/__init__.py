"""配置固定的召回组件，不拥有资源发布版本、ACL 或请求快照。"""

from .vector import VectorRetrievalMode, VectorRetriever

__all__ = ["VectorRetrievalMode", "VectorRetriever"]
