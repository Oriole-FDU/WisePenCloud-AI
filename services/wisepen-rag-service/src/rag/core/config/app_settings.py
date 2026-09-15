"""由 Nacos 加载的 P0 运行配置。"""

import asyncio
import threading

import yaml
from common.logger import error, info
from pydantic import BaseModel, ConfigDict

from .nacos import nacos_client_manager


class AppSettings(BaseModel):
    """当前已落地的 Mongo、LLM 与 Qdrant 运行配置。"""

    model_config = ConfigDict(extra="ignore")

    MONGODB_URL: str
    MONGODB_DB_NAME: str
    RESOURCE_MONGODB_DB_NAME: str | None = None
    FROM_SOURCE_SECRET: str = "APISIX-wX0iR6tY"
    LLM_BASE_URL: str
    LLM_API_KEY: str
    QUERY_MODEL: str
    EMBEDDING_MODEL: str
    EMBEDDING_DIMENSIONS: int
    ZERO_ENTROPY_API_KEY: str = ""
    RERANKER_MODEL: str = "zerank-2"
    RAG_RERANK_RELEVANCE_LOW_WATERMARK: float = 0.2
    RAG_RERANK_RELEVANCE_HIGH_WATERMARK: float = 0.6
    RAG_RERANK_UNCERTAIN_LIMIT: int = 3
    DOCUMENT_ENHANCEMENT_MAX_CONCURRENCY: int = 5
    DOCUMENT_ENHANCEMENT_ENABLED: bool = False
    KAFKA_ENABLED: bool = False
    KAFKA_BOOTSTRAP_SERVERS: str = ""
    KAFKA_DOCUMENT_READY_TOPIC: str = "wisepen-document-ready-topic"
    KAFKA_RAG_DOCUMENT_READY_GROUP_ID: str = "wisepen-rag-v3-document-ready-group"
    KAFKA_RESOURCE_ACL_RECALC_TOPIC: str = "wisepen-resource-acl-recalc-topic"
    KAFKA_RAG_ACL_RECALC_GROUP_ID: str = "wisepen-rag-v3-acl-recalc-group"
    KAFKA_RESOURCE_PHYSICAL_DESTROY_TOPIC: str = "wisepen-resource-physical-destroy-topic"
    KAFKA_RAG_RESOURCE_DESTROY_GROUP_ID: str = "wisepen-rag-v3-resource-destroy-group"
    KAFKA_RAG_DEAD_LETTER_TOPIC: str = "wisepen-rag-v3-failed-events-topic"
    KAFKA_RAG_MAX_DELIVERY_ATTEMPTS: int = 3
    KAFKA_RAG_RETRY_DELAY_SECONDS: float = 1.0
    GRAPH_ENABLED: bool = False
    NEO4J_URI: str = ""
    NEO4J_USERNAME: str = ""
    NEO4J_PASSWORD: str = ""
    QDRANT_HOST: str
    QDRANT_PORT: int = 6333
    QDRANT_PASSWORD: str = ""
    QDRANT_DOCUMENT_CHUNK_COLLECTION_NAME: str = "document_chunk_vectors"
    QDRANT_DOCUMENT_DENSE_VECTOR_NAME: str = "dense"
    QDRANT_DOCUMENT_SPARSE_VECTOR_NAME: str = "sparse"
    QDRANT_GRAPH_NODE_COLLECTION_NAME: str = "graph_node_vectors"
    QDRANT_GRAPH_EDGE_COLLECTION_NAME: str = "graph_edge_vectors"
    QDRANT_GRAPH_NODE_DENSE_VECTOR_NAME: str = "dense"
    QDRANT_GRAPH_EDGE_DENSE_VECTOR_NAME: str = "dense"
    QDRANT_GRAPH_EDGE_SPARSE_VECTOR_NAME: str = "sparse"

    @property
    def resource_mongodb_db_name(self) -> str:
        return self.RESOURCE_MONGODB_DB_NAME or self.MONGODB_DB_NAME


def _run_async(coroutine):
    """在独立线程运行 Nacos 协程，兼容 uvicorn 已创建事件循环的启动路径。"""
    result = None
    caught: Exception | None = None

    def run() -> None:
        nonlocal result, caught
        try:
            result = asyncio.run(coroutine)
        except Exception as exc:  # noqa: BLE001  # pragma: no cover - 需将任意协程失败送回启动线程
            caught = exc

    thread = threading.Thread(target=run)
    thread.start()
    thread.join()
    if caught is not None:
        raise caught
    return result


def load_settings() -> AppSettings:
    try:
        info("nacos app config pulling.")
        raw_yaml = _run_async(nacos_client_manager.pull_config())
        return AppSettings(**(yaml.safe_load(raw_yaml) if raw_yaml else {}))
    except Exception as exc:
        error("nacos app config pull failed.", exc=exc)
        raise


settings = load_settings()
