import asyncio
import threading
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict

from common.logger import error, info
from rag.core.config.nacos import nacos_client_manager


class AppSettings(BaseModel):
    model_config = ConfigDict()

    FROM_SOURCE_SECRET: str = "APISIX-wX0iR6tY"

    # LLM 配置
    LLM_BASE_URL: str
    LLM_API_KEY: str
    QUERY_MODEL: str
    EMBEDDING_MODEL: str
    EMBEDDING_DIMENSIONS: int = 4096
    ZERO_ENTROPY_API_KEY: str = ""
    RERANKER_MODEL: str = "zerank-2"

    # Kafka 配置
    KAFKA_BOOTSTRAP_SERVERS: str
    KAFKA_RESOURCE_ACL_RECALC_TOPIC: str = "wisepen-resource-acl-recalc-topic"
    KAFKA_RAG_ACL_RECALC_GROUP_ID: str = "wisepen-rag-acl-recalc-group"
    KAFKA_DOCUMENT_READY_TOPIC: str = "wisepen-document-ready-topic"
    KAFKA_RAG_DOCUMENT_READY_GROUP_ID: str = "wisepen-rag-document-ready-group"
    KAFKA_RESOURCE_PHYSICAL_DESTROY_TOPIC: str = (
        "wisepen-resource-physical-destroy-topic"
    )
    KAFKA_RAG_RESOURCE_DESTROY_GROUP_ID: str = "wisepen-rag-resource-destroy-group"

    # 数据库配置
    REDIS_URL: str
    MONGODB_URL: str
    MONGODB_DB_NAME: str
    RESOURCE_PERMISSION_MONGODB_DB_NAME: str = "wisepen_res_permission"
    QDRANT_HOST: str
    QDRANT_PORT: int = 6333
    QDRANT_PASSWORD: str = ""
    QDRANT_RAG_COLLECTION_NAME: str = "wisepen_rag_retrieval_chunks"
    QDRANT_RAG_DENSE_VECTOR_NAME: str = "dense"
    QDRANT_RAG_SPARSE_VECTOR_NAME: str = "sparse"
    QDRANT_RAG_BM25_TOKENIZER: Literal[
        "prefix",
        "whitespace",
        "word",
        "multilingual",
    ] = "multilingual"
    NEO4J_URI: str = "bolt://127.0.0.1:7687"
    NEO4J_USER: str = "neo4j"
    NEO4J_PASSWORD: str = "password123"
    NEO4J_DATABASE: str = "neo4j"

    # rag 参数配置
    KNOWLEDGE_GRAPH_EXTRACTION_MAX_CONCURRENCY: int = 5
    RAG_NAVIGATION_STATE_TTL_SECONDS: int = 24 * 3600
    RAG_RERANK_RELEVANCE_LOW_WATERMARK: float = 0.2  # 低于此值明确拒绝。
    RAG_RERANK_RELEVANCE_HIGH_WATERMARK: float = 0.6  # 达到此值可作为证据。
    RAG_RERANK_UNCERTAIN_LIMIT: int = 3  # 灰区最多返回的探索候选数。


def _run_async(coro):
    result, exc = None, None

    def _target():
        nonlocal result, exc
        try:
            result = asyncio.run(coro)
        except Exception as error:
            exc = error

    thread = threading.Thread(target=_target)
    thread.start()
    thread.join()
    if exc:
        raise exc
    return result


def load_settings() -> AppSettings:
    try:
        info("nacos app config pulling.")
        raw_yaml = _run_async(nacos_client_manager.pull_config())
        config_dict = yaml.safe_load(raw_yaml) if raw_yaml else {}
        return AppSettings(**(config_dict or {}))
    except Exception as exception:
        error("nacos app config pull failed.", exc=exception)
        raise


settings = load_settings()
