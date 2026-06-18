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
    """
    由 Nacos 提供的全量业务配置
    extra=forbid 校验, 预防字段错误
    """

    model_config = ConfigDict(extra="forbid")

    # LLM 默认网关配置（作为 fallback，主对话链路从 Provider 表动态获取）
    LLM_BASE_URL: str
    LLM_API_KEY: str
    DEFAULT_MODEL_ID: str

    # 模型配置 (请求依赖 LLM 默认网关配置)
    QUERY_MODEL: str = "openai/qwen3-4b"
    EMBEDDING_MODEL: str = "openai/qwen3-embedding-8b"
    EMBEDDING_DIMENSIONS: int = 1024

    # Memory相关模型
    MEMORY_LLM_MODEL: str
    MEMORY_EMBEDDING_MODEL: str
    MEMORY_RERANKER_ZE_MODEL: str
    ZERO_ENTROPY_API_KEY: str
    TOOL_CONTENT_RERANKER_ZE_MODEL: str = "zerank-1"
    TOOL_CONTENT_RERANKER_ZE_TOP_N: int | None = None
    EVIDENCE_RANKER_ZE_MODEL: str = "zerank-1"
    EVIDENCE_RANKER_ZE_TOP_N: int | None = None

    # 摘要模型
    SUMMARY_MODEL: str

    # 安全配置
    # 与 APISIX 网关约定的请求来源 token
    FROM_SOURCE_SECRET: str = "APISIX-wX0iR6tY"
    # 通用可解密密钥加密主密钥，使用 Fernet key；生产环境必须由配置中心或密钥系统提供。
    SECRET_ENCRYPTION_KEY: str = ""

    # Kafka 配置
    KAFKA_BOOTSTRAP_SERVERS: str
    KAFKA_TOKEN_CONSUMPTION_TOPIC: str = "wisepen-user-token-consumption-topic"

    # Redis / MongoDB / Qdrant 配置
    REDIS_URL: str
    MONGODB_URL: str
    MONGODB_DB_NAME: str
    QDRANT_HOST: str
    QDRANT_PORT: int = 6333
    QDRANT_PASSWORD: str

    # 参数配置

    # 模型 prompt budget
    # 默认模型上下文窗口大小，对齐 gpt-4o 的 128k 上下文
    CTX_TOKEN_LIMIT: int = 128000
    # 默认模型输出预留
    CTX_DEFAULT_OUTPUT_RESERVE_TOKENS: int = 4096
    # 模型 prompt budget 下限（避免异常情况）
    CTX_MIN_PROMPT_BUDGET_TOKENS: int = 1024

    # 上下文压缩
    # 最老的 (HIGH - LOW) 比例的对话将被送去摘要
    # 默认高水位线（触发阈值）：上下文累计 Token 达到此比例时触发摘要压缩
    CTX_HIGH_WATERMARK_RATIO: float = 0.8
    # 默认低水位线（安全退役线）：切分时按 Token 保留此比例以内的最新明细。
    CTX_LOW_WATERMARK_RATIO: float = 0.5

    # Redis 回填时从 MongoDB 拉取的历史消息条数上限
    CTX_FALLBACK_HISTORY_LIMIT: int = 20

    # 长期记忆
    # 默认长期记忆召回上限条目数
    CTX_LONG_TERM_MEMORY_LIMIT: int = 10
    # 默认长期记忆召回阈值
    CTX_LONG_TERM_MEMORY_THRESHOLD: int = 0.6

    # Agentic ReAct 循环
    # ReAct 最大推理迭代次数，防止工具调用产生无限循环
    AGENT_MAX_ITERATIONS: int = 5
    # 工具返回内容的字符截断上限（约 ~1000 token），防止超长结果撑爆后续迭代的上下文水位
    TOOL_RESULT_MAX_CHARS: int = 4000

    # PaddleOCR 云端服务配置
    # PaddleOCR 云端 API Token；工具行为参数在 tool_settings.py 中配置。
    PADDLE_OCR_TOKEN: str | None = "9926073f27dcb122bc45ac5e9103f0da54c9c167"
    PADDLE_OCR_API_URL: str = "https://paddleocr.aistudio-app.com/api/v2/ocr/jobs"
    PADDLE_OCR_MODEL: str = "PaddleOCR-VL-1.6"

    # Web Search 搜索引擎基础设施网关
    WEB_SEARCH_FOURGET_BASE_URL: str = "http://127.0.0.1:8088"
    WEB_SEARCH_EXA_BASE_URL: str = "https://api.exa.ai"
    WEB_SEARCH_TAVILY_BASE_URL: str = "https://api.tavily.com"
    WEB_SEARCH_ANYSEARCH_BASE_URL: str = "https://api.anysearch.com"
    WEB_SEARCH_SERPER_BASE_URL: str = "https://google.serper.dev"

    # 平台专属托管 Exa 分流策略控制, Q2 不接入链路
    WEB_SEARCH_PLATFORM_EXA_ENABLED: bool = False
    WEB_SEARCH_PLATFORM_EXA_API_KEY: str | None = "e4734bd6-3a94-458b-a90f-d5091aed436f"

    # 三方垂直领域服务与鉴权凭证 (学术 / 开源社区)
    OPENALEX_BASE_URL: str = "https://api.openalex.org"
    OPENALEX_API_KEY: str = "XgpyHsvgfEbhTmZ9E8rAFO"
    GITHUB_TOKEN: str = "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"

    # Skill 配置

    # 默认召回数量
    SKILL_MATCH_TOP_K: int = 20

    # 内部 RPC / 服务发现 配置
    # Nacos 服务发现客户端侧负载均衡策略：weighted_random | round_robin | random
    RPC_LB_STRATEGY: Literal["weighted_random", "round_robin", "random"] = "weighted_random"
    # 单次请求超时（秒）
    RPC_DEFAULT_TIMEOUT: float = 5.0
    # 单次调用最多额外重试次数（故障转移跨实例）；真实请求次数 = retries + 1
    RPC_DEFAULT_RETRIES: int = 2
    # ServiceDiscovery 本地缓存兜底 TTL（秒），即便订阅通道断连也会周期性强制 list
    SERVICE_DISCOVERY_CACHE_TTL_SECONDS: float = 30.0

    # OSS 资产本地磁盘缓存目录（运行期管理，GC 自动清理）
    OSS_CACHE_DIR: str = "/var/oss_cache"
    # 缓存文件 TTL：mtime 距今超过该秒数 → GC 清理（默认 6 小时）
    OSS_CACHE_TTL_SECONDS: int = 6 * 3600
    # GC 扫描周期（秒）
    OSS_CACHE_GC_INTERVAL_SECONDS: int = 30 * 60

    # ToolRunFileStore：工具产出临时文件工作区
    TOOL_RUN_FILE_ROOT: str = "/tmp/wisepen-tool-run-files"
    TOOL_RUN_FILE_REF_TTL_SECONDS: int = 6 * 3600
    TOOL_RUN_FILE_CLEANUP_GRACE_SECONDS: int = 10 * 60
    TOOL_RUN_FILE_MAX_BYTES: int = 50 * 1024 * 1024


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
