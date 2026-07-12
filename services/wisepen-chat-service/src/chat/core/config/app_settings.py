import yaml
import asyncio
import threading
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field

from chat.core.config.nacos import nacos_client_manager
from common.logger import error, info


class IflytekSpeechConfig(BaseModel):
    APP_ID: str = Field(..., min_length=1)
    API_KEY: str = Field(..., min_length=1)
    API_SECRET: str = Field(..., min_length=1)


class SpeechConfig(BaseModel):
    IFLYTEK: IflytekSpeechConfig | None = None


class AppSettings(BaseModel):
    """
    由 Nacos 提供的全量业务配置
    extra=forbid 校验, 预防字段错误
    """

    model_config = ConfigDict()

    # LLM 默认网关配置（作为 fallback，主对话链路从 Provider 表动态获取）
    LLM_BASE_URL: str
    LLM_API_KEY: str
    DEFAULT_MODEL_ID: str

    # 模型配置 (请求依赖 LLM 默认网关配置)
    # Memory相关模型
    MEMORY_LLM_MODEL: str
    MEMORY_EMBEDDING_MODEL: str
    MEMORY_RERANKER_ZE_MODEL: str
    ZERO_ENTROPY_API_KEY: str

    # 摘要模型
    SUMMARY_MODEL: str

    # 语音识别配置
    SPEECH_CONFIG: SpeechConfig | None = None

    # 安全配置
    # 与 APISIX 网关约定的请求来源 token
    FROM_SOURCE_SECRET: str = "APISIX-wX0iR6tY"

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
        info("nacos app config pulling.")
        raw_yaml = _run_async(nacos_client_manager.pull_config())
        config_dict = yaml.safe_load(raw_yaml) if raw_yaml else {}
        return AppSettings(**(config_dict or {}))
    except Exception as e:
        error("nacos app config pull failed.", exc=e)
        raise


settings = load_settings()

