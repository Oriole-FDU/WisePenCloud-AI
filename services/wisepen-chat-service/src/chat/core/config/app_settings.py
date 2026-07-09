import asyncio
import threading
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict

from chat.core.config.nacos import nacos_client_manager
from common.logger import error, info

SERVICE_ROOT = Path(__file__).resolve().parents[4]


class AppSettings(BaseModel):
    """由 Nacos 提供的全量业务配置，extra=forbid 校验预防字段错误。"""

    model_config = ConfigDict(extra="forbid")

    # ── LLM Gateway (默认网关，主对话链路从 Provider 表动态获取) ────────
    LLM_BASE_URL: str  # 默认 LLM 网关 base URL
    LLM_API_KEY: str  # 默认 LLM 网关 API key
    DEFAULT_MODEL_ID: str  # 默认主对话模型 ID

    # ── Tool-Use Small Models (工具性小模型调用栈) ───────────────────────
    # 以下模型专门服务于 application 层工具性轻量 LLM 调用：RAG answerability gate、
    # context indexing、web search ranking、工具内结构化推理等。
    # 它们必须与主对话模型、记忆模型、摘要模型隔离，禁止交叉复用。
    QUERY_MODEL: str  # 工具性轻量推理，低成本 + 快速响应
    EMBEDDING_MODEL: str  # 向量索引，与 MEMORY_EMBEDDING_MODEL 隔离
    EMBEDDING_DIMENSIONS: int  # 向量模型输出维度

    SUMMARY_MODEL: str  # 仅用于长文本摘要，必须单独维护，禁止挪作小模型调用

    # ── Memory Models (长期记忆模型) ───────────────────────────────────
    MEMORY_LLM_MODEL: str  # 记忆召回与总结所用 LLM
    MEMORY_EMBEDDING_MODEL: str  # 记忆向量嵌入模型
    MEMORY_RERANKER_ZE_MODEL: str  # 记忆重排模型
    ZERO_ENTROPY_API_KEY: str  # ZeroEntropy 服务 API key

    # ── Reranker Models (重排模型) ─────────────────────────────────────
    TOOL_CONTENT_RERANKER_ZE_MODEL: str  # ToolContentRead 重排模型
    TOOL_CONTENT_RERANKER_ZE_TOP_N: int | None = None  # ToolContentRead 重排返回 topN；None 表示不限制
    EVIDENCE_RANKER_ZE_MODEL: str  # RAG 证据重排模型
    EVIDENCE_RANKER_ZE_TOP_N: int | None = None  # RAG 证据重排返回 topN；None 表示不限制

    # ── Security (安全与鉴权) ──────────────────────────────────────────
    FROM_SOURCE_SECRET: str  # APISIX 网关请求来源 token
    SECRET_ENCRYPTION_KEY: str  # Fernet 加密主密钥

    # ── Message Queue (Kafka) ──────────────────────────────────────────
    KAFKA_BOOTSTRAP_SERVERS: str  # Kafka 引导地址
    KAFKA_TOKEN_CONSUMPTION_TOPIC: str = "wisepen-user-token-consumption-topic"  # Token 消费 topic
    KAFKA_DOCUMENT_READY_TOPIC: str = "wisepen-document-ready-topic"  # 文档就绪 topic
    KAFKA_RAG_DOCUMENT_READY_GROUP_ID: str = "wisepen-chat-rag-document-ready-group"  # RAG 文档入库消费者组
    KAFKA_RESOURCE_ACL_RECALC_TOPIC: str = "wisepen-resource-acl-recalc-topic"  # 资源 ACL 重算 topic
    KAFKA_RAG_ACL_RECALC_GROUP_ID: str = "wisepen-chat-rag-acl-recalc-group"  # RAG ACL 投影消费者组

    # ── Storage (Redis / MongoDB / Qdrant / Neo4j / Elasticsearch) ─────
    REDIS_URL: str  # Redis 连接串

    MONGODB_URL: str  # MongoDB 连接串
    MONGODB_DB_NAME: str  # MongoDB 数据库名

    QDRANT_HOST: str  # Qdrant 主机地址
    QDRANT_PORT: int  # Qdrant 端口
    QDRANT_PASSWORD: str  # Qdrant 密码

    QDRANT_RAG_COLLECTION_NAME: str = "wisepen_rag_child_chunks"  # RAG child chunk Qdrant collection
    QDRANT_RAG_BM25_TOKENIZER: Literal[
        "prefix",
        "whitespace",
        "word",
        "multilingual",
    ] = "multilingual"  # Qdrant BM25 tokenizer 类型

    NEO4J_URI: str = "neo4j://localhost:7687"  # Neo4j 连接 URI
    NEO4J_USERNAME: str = "neo4j"  # Neo4j 用户名
    NEO4J_PASSWORD: str = "password123"  # Neo4j 密码

    ELASTIC_SEARCH_BASE_URL: str  # Elasticsearch 基础 URL；空串表示未启用
    ELASTIC_SEARCH_USERNAME: str  # Elasticsearch 用户名
    ELASTIC_SEARCH_PASSWORD: str  # Elasticsearch 密码

    ELASTIC_SEARCH_RAG_INDEX_NAME: str = "wisepen_rag_child_chunks"  # RAG child chunk strict prefilter 索引名

    # ── Context Window (模型上下文窗口预算) ────────────────────────────
    CTX_TOKEN_LIMIT: int = 128000  # 上下文窗口大小，对齐 gpt-4o 128k
    CTX_DEFAULT_OUTPUT_RESERVE_TOKENS: int = 4096  # 模型输出预留 token 数
    CTX_MIN_PROMPT_BUDGET_TOKENS: int = 1024  # prompt budget 下限

    # ── Context Compression (上下文压缩水位线) ─────────────────────────
    CTX_HIGH_WATERMARK_RATIO: float = 0.8  # 高水位线：触发摘要压缩的 token 比例
    CTX_LOW_WATERMARK_RATIO: float = 0.5  # 低水位线：压缩后保留的最新明细比例
    CTX_FALLBACK_HISTORY_LIMIT: int = 20  # Redis 回填时从 MongoDB 拉取的历史消息上限

    # ── Long-Term Memory (长期记忆召回) ────────────────────────────────
    CTX_LONG_TERM_MEMORY_LIMIT: int = 10  # 召回上限条目数
    CTX_LONG_TERM_MEMORY_THRESHOLD: int = 0.6  # 召回相似度阈值

    # ── Agent Loop (ReAct 循环控制) ────────────────────────────────────
    AGENT_MAX_ITERATIONS: int = 20  # 最大推理迭代次数
    TOOL_RESULT_MAX_CHARS: int = 4000  # 工具返回内容截断上限 (~1000 token)
    TOOL_CONTENT_DEFAULT_TTL_SECONDS: int = 1800  # ToolContentStore 内容缓存 TTL
    TOOL_CONTENT_MAX_CHARS: int = 20_000_000  # ToolContentStore 单段文本入库字符上限
    TOOL_CONTENT_WINDOW_MAX_CHARS: int = 100_000  # ToolContentRead 单个读取窗口字符上限

    # —— RAG ——————————————————————————————————————————————————————————
    RAG_INGESTION_DETERMINISTIC_CACHE_TTL_SECONDS: int = 7 * 24 * 3600  # RAG 入库确定性中间结果缓存 TTL
    RAG_EVIDENCE_MATERIALIZATION_CACHE_TTL_SECONDS: int = 600  # RAG 已授权 evidence 物化缓存 TTL
    RAG_GRAPH_ENHANCEMENT_CACHE_TTL_SECONDS: int = 600  # RAG Neo4j 图增强结果缓存 TTL
    RAG_GRAPH_VERSION: str = "v1"  # RAG 图投影版本，变更后让旧 graph cache 自然 miss
    RAG_ONTOLOGY_SCHEMA_VERSION: str = "v1"  # RAG ontology schema 版本，变更后让旧 graph cache 自然 miss
    RAG_KNOWLEDGE_SEARCH_TOP_K: int = 8  # RAG 返回给 answerability 的证据条数
    RAG_KNOWLEDGE_SEARCH_CANDIDATE_LIMIT: int = 80  # RAG 召回和排序中间窗口
    RAG_KNOWLEDGE_SEARCH_ELASTIC_PREFILTER_LIMIT: int = 1000  # RAG Elastic 关键词前置过滤窗口
    RAG_QDRANT_SEMANTIC_DENSE_RRF_WEIGHT: float = 2.0  # semantic profile dense 通道权重
    RAG_QDRANT_SEMANTIC_SPARSE_RRF_WEIGHT: float = 0.75  # semantic profile sparse 通道权重
    RAG_QDRANT_LEXICAL_DENSE_RRF_WEIGHT: float = 0.75  # lexical profile dense 通道权重
    RAG_QDRANT_LEXICAL_SPARSE_RRF_WEIGHT: float = 2.0  # lexical profile sparse 通道权重

    # ── PaddleOCR Cloud (OCR 云端服务网关) ─────────────────────────────
    PADDLE_OCR_TOKEN: str  # PaddleOCR 服务 token
    PADDLE_OCR_API_URL: str  # PaddleOCR 服务 API URL
    PADDLE_OCR_MODEL: str  # PaddleOCR 模型名

    # ── Web Search Gateways (搜索引擎基础设施网关) ──────────────────────
    WEB_SEARCH_FOURGET_BASE_URL: str = "http://127.0.0.1:8088"  # Fourget 搜索网关
    WEB_SEARCH_EXA_BASE_URL: str = "https://api.exa.ai"  # Exa 搜索网关
    WEB_SEARCH_TAVILY_BASE_URL: str = "https://api.tavily.com"  # Tavily 搜索网关
    WEB_SEARCH_ANYSEARCH_BASE_URL: str = "https://api.anysearch.com"  # AnySearch 搜索网关
    WEB_SEARCH_BAIDU_QIANFAN_BASE_URL: str = "https://qianfan.baidubce.com"  # 百度千帆搜索网关
    WEB_SEARCH_PLATFORM_MEMBER_PROVIDER: Literal[
                                             "exa",
                                             "tavily",
                                             "anysearch",
                                             "baidu_qianfan",
                                         ] | None = None  # 会员平台源当前路由到的 provider；None 表示未启用
    WEB_SEARCH_PLATFORM_MEMBER_API_KEY: str | None = None  # 会员平台源使用的平台密钥

    # ── Internal Service Gateways (内部服务网关) ────────────────────────
    NOTE_COLLAB_GATEWAY_BASE_URL: str  # Note Collab 服务内部网关 base URL

    # ── Third-Party Credentials (三方垂直领域) ─────────────────────

    # ── Skill (技能召回) ───────────────────────────────────────────────
    SKILL_MATCH_TOP_K: int = 20  # 默认召回数量

    # ── RPC & Service Discovery (内部 RPC 与服务发现) ──────────────────
    RPC_LB_STRATEGY: Literal["weighted_random", "round_robin", "random"] = "weighted_random"  # 负载均衡策略
    RPC_DEFAULT_TIMEOUT: float = 5.0  # 单次请求超时 (秒)
    RPC_DEFAULT_RETRIES: int = 2  # 额外重试次数，真实请求 = retries + 1
    SERVICE_DISCOVERY_CACHE_TTL_SECONDS: float = 30.0  # 本地缓存兜底 TTL (秒)

    # ── OSS Cache (对象存储本地磁盘缓存) ───────────────────────────────
    OSS_CACHE_DIR: str = "/var/oss_cache"  # 本地缓存目录
    OSS_CACHE_TTL_SECONDS: int = 6 * 3600  # 缓存文件 TTL (6h)
    OSS_CACHE_GC_INTERVAL_SECONDS: int = 30 * 60  # GC 扫描周期 (30min)

    # ── Tool Run File Store (工具产出临时文件工作区) ────────────────────
    TOOL_RUN_FILE_ROOT: str = "/tmp/wisepen-tool-run-files"  # 工作区根目录
    # TTL / 容量 / 宽限期等稳定行为默认值由 ToolRunFileStore 就近定义。


def _run_async(coro):
    """在新线程的独立事件循环中执行协程，兼容 uvicorn 启动时已有运行中事件循环的场景。"""
    result, error = None, None

    def _target():
        nonlocal result, error
        try:
            result = asyncio.run(coro)
        except Exception as exc:
            error = exc

    t = threading.Thread(target=_target)
    t.start()
    t.join()
    if error:
        raise error
    return result


def load_settings() -> AppSettings:
    try:
        info("nacos app config pulling.")
        raw_yaml = _run_async(nacos_client_manager.pull_config())
        config_dict = yaml.safe_load(raw_yaml) if raw_yaml else {}
        return AppSettings(**(config_dict or {}))
    except Exception as e:
        error("nacos app config pull failed.", e=e)
        raise


settings = load_settings()
