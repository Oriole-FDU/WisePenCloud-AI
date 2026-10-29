"""扩展交给平台的实际装配产物；平台统一初始化、挂载与清理。"""

from dataclasses import dataclass, field

from fastapi import FastAPI

from rag.domain.storage.vector import VectorCollection
from rag.ingestion import IngestionPipeline


@dataclass(slots=True)
class Extension:
    """保留摄入组件、额外向量存储及已注入用例的 API，不持有新连接。"""

    pipeline: IngestionPipeline
    vector_collections: list[VectorCollection] = field(default_factory=list)
    api: FastAPI | None = None
