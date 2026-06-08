import asyncio
import os
import threading
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel

from chat.core.config.bootstrap_settings import bootstrap_settings
from common.cloud.nacos_client import nacos_client_manager
from common.logger import log_error, log_event

SERVICE_ROOT = Path(__file__).resolve().parents[4]


class AppSettings(BaseModel):
    """全量应用配置，字段扁平化，外部统一使用 settings.FIELD_NAME 访问。"""

    # ==========================================
    # 1. 应用核心与网络安全 (Core & Security)
    # ==========================================
    APP_NAME: str
    SERVICE_NAME: str
    SERVICE_HOST: str
    SERVICE_PORT: int
    DEV: bool
    LOG_LEVEL: str

    # 安全配置：与 APISIX 网关约定的防绕过 Token
    FROM_SOURCE_SECRET: str = "APISIX-wX0iR6tY"


    # ==========================================
    # 2. 数据库仓储基建集群 (Storage Infrastructure)
    # ==========================================
    # Redis 缓存与状态线
    REDIS_URL: str

    # MongoDB 历史会话与关系主库
    MONGODB_URL: str
    MONGODB_DB_NAME: str

    # Qdrant 长期语义记忆向量数据库
    QDRANT_HOST: str
    QDRANT_PORT: int = 6333
    QDRANT_PASSWORD: str

    # Elasticsearch 关键词与精确检索库
    ELASTICSEARCH_URIS: str = "http://wisepen-dev-server:9200"
    ELASTICSEARCH_USERNAME: str = "elastic"
    ELASTICSEARCH_PASSWORD: str = "root"
    RAG_ELASTICSEARCH_INDEX_NAME: str = "wisepen_rag_keyword_chunks"


    # ==========================================
    # 3. 高并发消息队列 (Messaging Queue)
    # ==========================================
    # Kafka 引导服务器集群地址
    KAFKA_BOOTSTRAP_SERVERS: str = "wisepen-dev-server:9094"
    # 用户大模型 Token 消耗统计专线 Topic
    KAFKA_TOKEN_CONSUMPTION_TOPIC: str = "wisepen-user-token-consumption-topic"


    # ==========================================
    # 4. 内部 RPC 与服务发现 (RPC & Service Discovery)
    # ==========================================
    # Nacos 服务发现客户端侧负载均衡策略：weighted_random | round_robin | random
    RPC_LB_STRATEGY: Literal["weighted_random", "round_robin", "random"] = (
        "weighted_random"
    )
    # 单次请求超时（秒）
    RPC_DEFAULT_TIMEOUT: float = 5.0
    # 单次调用最多额外重试次数（故障转移跨实例）；真实请求次数 = retries + 1
    RPC_DEFAULT_RETRIES: int = 2
    # ServiceDiscovery 本地缓存兜底 TTL（秒），即便订阅通道断连也会周期性强制 list
    SERVICE_DISCOVERY_CACHE_TTL_SECONDS: float = 30.0


    # ==========================================
    # 5. 模型提供商网关与底层 Model 选型 (LLM Providers & Models)
    # ==========================================
    # 门面大模型 Fallback 网关
    LLM_BASE_URL: str
    LLM_API_KEY: str

    # Chat 默认模型
    DEFAULT_MODEL_ID: int = 1

    # ZeroEntropy 检索增强基础网关
    ZERO_ENTROPY_BASE_URL: str = "https://api.zeroentropy.dev/v1"
    ZERO_ENTROPY_API_KEY: str
    ZERO_ENTROPY_TIMEOUT_SECONDS: float = 30.0

    # 记忆线模型选型 (Memory)
    MEMORY_LLM_MODEL: str = "gpt-4o-mini"
    MEMORY_EMBEDDING_MODEL: str = "text-embedding-3-large"
    MEMORY_RERANKER_ZE_MODEL: str = "zerank-1"

    # 压缩线模型选型 (Summary)
    SUMMARY_MODEL: str = "openai/gemini-3-flash-preview"

    # 检索线模型选型 (RAG & Embedding & Reranker)
    RAG_CONTEXT_MODEL: str = "openai/qwen3-4b"
    RAG_CONTEXT_MODEL_VERSION: str = "qwen3-4b"
    RAG_DENSE_EMBEDDING_MODEL: str = "openai/qwen3-embedding-8b"
    RAG_DENSE_EMBEDDING_MODEL_VERSION: str = "qwen3-embedding-8b"
    RAG_RERANKER_ZE_MODEL: str = "zerank-1"


    # ==========================================
    # 6. 运行时策略与水位控制 (Runtime Strategies & Watermarks)
    # ==========================================
    # Token 动态滑动窗口与双水位压缩
    CTX_TOKEN_LIMIT: int = 128000
    CTX_DEFAULT_OUTPUT_RESERVE_TOKENS: int = 4096
    CTX_MIN_PROMPT_BUDGET_TOKENS: int = 1024
    CTX_HIGH_WATERMARK_RATIO: float = 0.8
    CTX_LOW_WATERMARK_RATIO: float = 0.5
    CTX_FALLBACK_HISTORY_LIMIT: int = 20

    # Agentic ReAct 循环控制
    AGENT_MAX_ITERATIONS: int = 15
    TOOL_RESULT_MAX_CHARS: int = 4000

    # RAG 推理超参（Context 生成与重排序）
    RAG_CONTEXT_MAX_TOKENS: int = 192
    RAG_CONTEXT_TEMPERATURE: float = 0.0
    RAG_RERANKER_TOP_N: int = 20
    RAG_RETRIEVAL_CHANNEL_TIMEOUT_SECONDS: float = 8.0

    # RAG 管道版本控制（Chunking / Indexing / Embedding Pipeline）
    RAG_CHUNKER_VERSION: str = "recursive-character-v1"
    RAG_SEMANTIC_INDEXING_TEXT_VERSION: str = "semantic-indexing-text-v1"
    RAG_KEYWORD_INDEXING_VERSION: str = "keyword-indexing-v1"
    RAG_IDENTIFIER_EXTRACTOR_VERSION: str = "identifier-extractor-disabled-v1"
    RAG_SPARSE_EMBEDDING_MODEL_VERSION: str = "qdrant-bm25-v1"
    RAG_CONTEXTUAL_INDEXING_VERSION: str = "context-indexing-v1"
    RAG_CONTEXT_PROMPT_VERSION: str = "context-prompt-v1"
    # 索引管道消费组（RAG 专属）
    RAG_INDEX_CONSUMER_GROUP: str = "wisepen-rag-indexers"
    # Web 进程是否内嵌启动 RAG 索引 Worker；生产建议使用独立 worker 进程
    RAG_INDEX_WORKER_IN_PROCESS: bool = False
    RAG_INDEX_MAX_ATTEMPTS: int = 5
    # Qdrant Collection 名称
    RAG_QDRANT_COLLECTION_NAME: str = "wisepen-qdrant-rag"
    RAG_DENSE_VECTOR_SIZE: int = 2048

    # ==========================================
    # 7. Web 搜索与抓取 (Web Search & Fetch)
    # ==========================================
    # 各搜索引擎 / 聚合代理网关基础 URL
    FOURGET_BASE_URL: str = "http://fourget:80"
    SERPER_BASE_URL: str = "https://google.serper.dev"
    TAVILY_BASE_URL: str = "https://api.tavily.com"
    BRAVE_SEARCH_BASE_URL: str = "https://api.search.brave.com"
    SERPAPI_BASE_URL: str = "https://serpapi.com"
    EXA_BASE_URL: str = "https://api.exa.ai"
    PERPLEXITY_BASE_URL: str = "https://api.perplexity.ai"
    ANYSEARCH_BASE_URL: str = "https://api.anysearch.com"
    WIKIPEDIA_BASE_URL_TEMPLATE: str = "https://en.wikipedia.org"
    # Web 页面全文抓取代理
    STEEL_BASE_URL: str = "http://steel-browser:3000"


    # ==========================================
    # 8. 外部工具服务 (External Tool Services)
    # ==========================================
    # 数学推理沙箱 Worker
    SAGE_MATH_WORKER_URL: str = "http://sage-math-worker:8000"


    # ==========================================
    # 9. Skill 插件系统与资产目录 (Skill System & Assets)
    # ==========================================
    # 资产路径与物理缓存定义
    SKILL_ASSETS_CACHE_DIR: str = "dev_fixtures/skill_bundles"
    SKILL_OSS_CACHE_DIR: str = "/var/skill_oss_cache"

    @property
    def SKILL_ASSETS_CACHE_PATH(self) -> Path:
        path = Path(self.SKILL_ASSETS_CACHE_DIR)
        if path.is_absolute():
            return path
        return (SERVICE_ROOT / path).resolve()

    @property
    def SKILL_OSS_CACHE_PATH(self) -> Path:
        path = Path(self.SKILL_OSS_CACHE_DIR)
        if path.is_absolute():
            return path
        return (SERVICE_ROOT / path).resolve()

    # Skill 系统行为控制、超时与 GC 调度
    # 缓存文件 TTL：mtime 距今超过该秒数 → GC 清理（默认 6 小时）
    SKILL_OSS_CACHE_TTL_SECONDS: int = 6 * 3600
    # GC 扫描周期（秒）
    SKILL_OSS_CACHE_GC_INTERVAL_SECONDS: int = 30 * 60
    # Matcher 每轮给 LLM 暴露的 skill 候选上限（受控披露，防 LLM 误加载）
    SKILL_MATCH_TOP_K: int = 2
    # Skill 元数据缓存 TTL（秒）。用户/Java 端发布的新 Skill 最坏需等 TTL 才被当前副本感知。
    SKILL_CACHE_TTL_SECONDS: int = 30


def _load_env_overrides() -> dict[str, str]:
    return {
        key: value
        for key in AppSettings.model_fields
        if (value := os.getenv(key)) is not None
    }


def _run_async(coro):
    """在新线程的独立事件循环中执行协程，兼容 uvicorn 启动时已有运行中事件循环的场景。"""
    result, exc = None, None

    def _target():
        nonlocal result, exc
        try:
            result = asyncio.run(coro)
        except Exception as e:
            exc = e

    t = threading.Thread(target=_target)
    t.start()
    t.join()
    if exc:
        raise exc
    return result


def load_settings() -> AppSettings:
    try:
        log_event("从 Nacos 拉取核心业务配置")
        raw_yaml = _run_async(nacos_client_manager.pull_config())
        config_dict = yaml.safe_load(raw_yaml) if raw_yaml else {}
        env_config = _load_env_overrides()
        # Precedence: Nacos > container environment > bootstrap defaults.
        full_config = {
            **bootstrap_settings.model_dump(),
            **env_config,
            **config_dict,
        }
        if "DEV" not in full_config:
            full_config["DEV"] = bootstrap_settings.IS_DEV
        return AppSettings(**full_config)
    except Exception as e:
        log_error("Nacos 配置拉取或解析", e)
        raise


settings = load_settings()
