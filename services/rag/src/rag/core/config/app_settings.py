"""可显式构造的运行配置；默认入口通过 load_settings 从 Nacos 加载。"""

import asyncio
import threading

import yaml
from common.logger import error
from pydantic import BaseModel, ConfigDict, Field, model_validator


class AppSettings(BaseModel):
    """平台默认依赖及后台任务配置；垂类本体和路由不属于此配置。"""

    model_config = ConfigDict(extra="ignore")

    # MongoDB：主库与可选资源库
    MONGODB_URL: str
    MONGODB_DB_NAME: str
    RESOURCE_MONGODB_DB_NAME: str | None = None

    # Qdrant：URL、密钥与三套向量集合
    QDRANT_URL: str
    QDRANT_API_KEY: str | None = None
    VECTOR_COLLECTION: str = "document_chunk_vectors_v4"
    GRAPH_ENTITY_VECTOR_COLLECTION: str = "graph_entity_source_vectors_v4"
    GRAPH_RELATION_VECTOR_COLLECTION: str = "graph_relation_source_vectors_v4"

    # 嵌入与精排模型
    EMBEDDING_BASE_URL: str
    EMBEDDING_API_KEY: str
    EMBEDDING_MODEL: str
    EMBEDDING_DIMENSIONS: int = Field(gt=0)
    QWEN_RERANKER_MODEL: str
    QWEN_API_KEY: str

    # 上下文增强（可选）；启用时需配齐三项指令模型
    ENABLE_CONTEXTUALIZE: bool = False
    INSTRUCTION_BASE_URL: str | None = None
    INSTRUCTION_API_KEY: str | None = None
    INSTRUCTION_MODEL: str | None = None

    # Neo4j 图存储（可选）；启用时需配齐连接信息
    ENABLE_GRAPH_STORAGE: bool = False
    NEO4J_URI: str | None = None
    NEO4J_USERNAME: str | None = None
    NEO4J_PASSWORD: str | None = None
    NEO4J_DATABASE: str = "neo4j"
    NEO4J_GRAPH_NAME: str = "rag_v4"

    # 后台 GC 任务
    GC_INTERVAL_SECONDS: float = Field(default=600, gt=0, allow_inf_nan=False)
    GC_BATCH_SIZE: int = Field(default=100, gt=0)

    # Kafka 消费（可选）；启用时需配 bootstrap servers
    KAFKA_ENABLED: bool = False
    KAFKA_BOOTSTRAP_SERVERS: str = ""
    KAFKA_MAX_POLL_INTERVAL_MS: int = 300_000
    KAFKA_RESOURCE_ACL_RECALC_TOPIC: str = "wispen-resource-acl-recalc-topic"
    KAFKA_RAG_ACL_RECALC_GROUP_ID: str = "wispen-rag-v4-acl-recalc-group"
    KAFKA_DOCUMENT_READY_TOPIC: str = "wisepen-document-ready-topic"
    KAFKA_RESOURCE_PHYSICAL_DESTROY_TOPIC: str = (
        "wisepen-resource-physical-destroy-topic"
    )
    KAFKA_RAG_PUBLICATION_GROUP_ID: str = "wispen-rag-v4-publication-group"

    @property
    def resource_mongodb_db_name(self) -> str:
        # 未单独配置资源库时回落到主库
        return self.RESOURCE_MONGODB_DB_NAME or self.MONGODB_DB_NAME

    @model_validator(mode="after")
    def check_capabilities(self) -> "AppSettings":
        # 只校验装配所需配置与物理空间隔离，不重复底层 SDK 的协议校验。
        required = [
            self.MONGODB_URL,
            self.MONGODB_DB_NAME,
            self.QDRANT_URL,
            self.EMBEDDING_BASE_URL,
            self.EMBEDDING_API_KEY,
            self.EMBEDDING_MODEL,
            self.QWEN_RERANKER_MODEL,
            self.QWEN_API_KEY,
        ]

        # 启用上下文增强或已配置任一指令字段时，要求三项齐全
        instruction = [
            self.INSTRUCTION_BASE_URL,
            self.INSTRUCTION_API_KEY,
            self.INSTRUCTION_MODEL,
        ]
        if self.ENABLE_CONTEXTUALIZE or any(instruction):
            required.extend(instruction)

        # 按启用的能力追加必填项
        if self.ENABLE_GRAPH_STORAGE:
            required.extend([self.NEO4J_URI, self.NEO4J_USERNAME, self.NEO4J_PASSWORD])
        if self.KAFKA_ENABLED:
            required.append(self.KAFKA_BOOTSTRAP_SERVERS)

        # 必填项不得为空或空白
        if any(value is None or not value.strip() for value in required):
            raise ValueError(
                "enabled platform capabilities require complete nonblank configuration"
            )

        # 三套向量集合必须非空且互不相同
        collections = [
            self.VECTOR_COLLECTION,
            self.GRAPH_ENTITY_VECTOR_COLLECTION,
            self.GRAPH_RELATION_VECTOR_COLLECTION,
        ]
        if any(not name.strip() for name in collections) or len(set(collections)) != 3:
            raise ValueError(
                "platform vector collections must be nonblank and distinct"
            )

        # ACL 与 publication 消费者必须使用不同 group
        if self.KAFKA_RAG_ACL_RECALC_GROUP_ID == self.KAFKA_RAG_PUBLICATION_GROUP_ID:
            raise ValueError("ACL and publication consumers require distinct groups")
        return self


def _run_async(coroutine):
    """在新线程里跑协程，把结果与异常搬回同步调用方。"""
    result = None
    caught: Exception | None = None

    def run() -> None:
        nonlocal result, caught
        try:
            result = asyncio.run(coroutine)
        except Exception as exc:  # noqa: BLE001
            caught = exc

    thread = threading.Thread(target=run)
    thread.start()
    thread.join()
    if caught is not None:
        raise caught
    return result


def load_settings() -> AppSettings:
    """默认运行配置来自 Nacos；显式构造 AppSettings 可用于 provider 覆盖。"""
    from .nacos import nacos_client_manager

    try:
        raw_yaml = _run_async(nacos_client_manager.pull_config())
        values = yaml.safe_load(raw_yaml) if raw_yaml else {}
        return AppSettings(**(values or {}))
    except Exception as exc:
        error("nacos app config pull failed.", exc=exc)
        raise


settings = load_settings()