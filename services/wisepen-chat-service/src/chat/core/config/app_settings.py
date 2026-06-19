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
    LLM_BASE_URL: str
    LLM_API_KEY: str
    DEFAULT_MODEL_ID: str

    # ── Model Selection (模型选型) ─────────────────────────────────────
    QUERY_MODEL: str = "openai/gpt-4o-mini"
    EMBEDDING_MODEL: str = "openai/qwen3-embedding-8b"
    EMBEDDING_DIMENSIONS: int = 1024
    SUMMARY_MODEL: str

    # ── Memory Models (长期记忆模型) ───────────────────────────────────
    MEMORY_LLM_MODEL: str
    MEMORY_EMBEDDING_MODEL: str
    MEMORY_RERANKER_ZE_MODEL: str
    ZERO_ENTROPY_API_KEY: str

    # ── Reranker Models (重排模型) ─────────────────────────────────────
    TOOL_CONTENT_RERANKER_ZE_MODEL: str = "zerank-1"
    TOOL_CONTENT_RERANKER_ZE_TOP_N: int | None = None
    EVIDENCE_RANKER_ZE_MODEL: str = "zerank-1"
    EVIDENCE_RANKER_ZE_TOP_N: int | None = None

    # ── Security (安全与鉴权) ──────────────────────────────────────────
    FROM_SOURCE_SECRET: str = "APISIX-wX0iR6tY"                          # APISIX 网关请求来源 token
    SECRET_ENCRYPTION_KEY: str = "JDW8fLFPrOMRlywslZKMQoy3SOzfkB0bXJMKl3_O-Kw="  # Fernet 加密主密钥

    # ── Message Queue (Kafka) ──────────────────────────────────────────
    KAFKA_BOOTSTRAP_SERVERS: str
    KAFKA_TOKEN_CONSUMPTION_TOPIC: str = "wisepen-user-token-consumption-topic"

    # ── Storage (Redis / MongoDB / Qdrant) ─────────────────────────────
    REDIS_URL: str
    MONGODB_URL: str
    MONGODB_DB_NAME: str
    QDRANT_HOST: str
    QDRANT_PORT: int = 6333
    QDRANT_PASSWORD: str

    # ── Context Window (模型上下文窗口预算) ────────────────────────────
    CTX_TOKEN_LIMIT: int = 128000                                        # 上下文窗口大小，对齐 gpt-4o 128k
    CTX_DEFAULT_OUTPUT_RESERVE_TOKENS: int = 4096                        # 模型输出预留 token 数
    CTX_MIN_PROMPT_BUDGET_TOKENS: int = 1024                             # prompt budget 下限

    # ── Context Compression (上下文压缩水位线) ─────────────────────────
    CTX_HIGH_WATERMARK_RATIO: float = 0.8                                # 高水位线：触发摘要压缩的 token 比例
    CTX_LOW_WATERMARK_RATIO: float = 0.5                                 # 低水位线：压缩后保留的最新明细比例
    CTX_FALLBACK_HISTORY_LIMIT: int = 20                                 # Redis 回填时从 MongoDB 拉取的历史消息上限

    # ── Long-Term Memory (长期记忆召回) ────────────────────────────────
    CTX_LONG_TERM_MEMORY_LIMIT: int = 10                                 # 召回上限条目数
    CTX_LONG_TERM_MEMORY_THRESHOLD: int = 0.6                            # 召回相似度阈值

    # ── Agent Loop (ReAct 循环控制) ────────────────────────────────────
    AGENT_MAX_ITERATIONS: int = 100                                  # 最大推理迭代次数
    TOOL_RESULT_MAX_CHARS: int = 4000                                    # 工具返回内容截断上限 (~1000 token)

    # ── PaddleOCR Cloud (OCR 云端服务网关) ─────────────────────────────
    PADDLE_OCR_TOKEN: str | None = "9926073f27dcb122bc45ac5e9103f0da54c9c167"
    PADDLE_OCR_API_URL: str = "https://paddleocr.aistudio-app.com/api/v2/ocr/jobs"
    PADDLE_OCR_MODEL: str = "PaddleOCR-VL-1.6"

    # ── Web Search Gateways (搜索引擎基础设施网关) ──────────────────────
    WEB_SEARCH_FOURGET_BASE_URL: str = "http://127.0.0.1:8088"
    WEB_SEARCH_EXA_BASE_URL: str = "https://api.exa.ai"
    WEB_SEARCH_TAVILY_BASE_URL: str = "https://api.tavily.com"
    WEB_SEARCH_ANYSEARCH_BASE_URL: str = "https://api.anysearch.com"
    WEB_SEARCH_SERPER_BASE_URL: str = "https://google.serper.dev"
    WEB_SEARCH_PLATFORM_EXA_ENABLED: bool = False                        # 平台专属 Exa 分流开关
    WEB_SEARCH_PLATFORM_EXA_API_KEY: str | None = "e4734bd6-3a94-458b-a90f-d5091aed436f"

    # ── Third-Party Credentials (三方垂直领域鉴权) ─────────────────────
    OPENALEX_BASE_URL: str = "https://api.openalex.org"
    OPENALEX_API_KEY: str = "XgpyHsvgfEbhTmZ9E8rAFO"
    GITHUB_TOKEN: str

    # ── Skill (技能召回) ───────────────────────────────────────────────
    SKILL_MATCH_TOP_K: int = 20                                          # 默认召回数量

    # ── RPC & Service Discovery (内部 RPC 与服务发现) ──────────────────
    RPC_LB_STRATEGY: Literal["weighted_random", "round_robin", "random"] = "weighted_random"
    RPC_DEFAULT_TIMEOUT: float = 5.0                                     # 单次请求超时 (秒)
    RPC_DEFAULT_RETRIES: int = 2                                         # 额外重试次数，真实请求 = retries + 1
    SERVICE_DISCOVERY_CACHE_TTL_SECONDS: float = 30.0                    # 本地缓存兜底 TTL (秒)

    # ── OSS Cache (对象存储本地磁盘缓存) ───────────────────────────────
    OSS_CACHE_DIR: str = "/var/oss_cache"                                # 本地缓存目录
    OSS_CACHE_TTL_SECONDS: int = 6 * 3600                                # 缓存文件 TTL (6h)
    OSS_CACHE_GC_INTERVAL_SECONDS: int = 30 * 60                         # GC 扫描周期 (30min)

    # ── Tool Run File Store (工具产出临时文件工作区) ────────────────────
    TOOL_RUN_FILE_ROOT: str = "/tmp/wisepen-tool-run-files"              # 工作区根目录
    # 行为参数（TTL / 容量 / 宽限期）由 tool_settings.py 统一管控


def _run_async(coro):
    """在新线程的独立事件循环中执行协程，兼容 uvicorn 启动时已有运行中事件循环的场景。"""
    result, e = None, None

    def _target():
        nonlocal result, e
        try:
            result = asyncio.run(coro)
        except Exception as e:
            e = e

    t = threading.Thread(target=_target)
    t.start()
    t.join()
    if e:
        raise e
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
