import yaml
import asyncio
import threading
from pathlib import Path
from typing import Literal
from pydantic import BaseModel, ConfigDict

from common.cloud.nacos_client import nacos_client_manager
from common.logger import log_event, log_error

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
    DEFAULT_MODEL_ID: int = 1

    # 模型配置 (请求依赖 LLM 默认网关配置)
    # Memory相关模型
    MEMORY_LLM_MODEL: str
    MEMORY_EMBEDDING_MODEL: str
    MEMORY_RERANKER_ZE_MODEL: str
    ZERO_ENTROPY_API_KEY: str

    # 摘要模型
    SUMMARY_MODEL: str

    # 安全配置
    # 与 APISIX 网关约定的请求来源 token
    FROM_SOURCE_SECRET: str

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

    # Token 动态滑动窗口 + 双水位压缩
    # 模型上下文窗口总大小（token 数），默认对齐 gpt-4o 的 128k 上下文
    CTX_TOKEN_LIMIT: int = 128000
    # 高水位线（触发阈值）：上下文累计 Token 达到此比例时触发摘要压缩
    CTX_HIGH_WATERMARK_RATIO: float = 0.8
    # 低水位线（安全退役线）：切分时按 Token 保留此比例以内的最新明细。
    # 最老的 (HIGH - LOW) 比例的 Token 对应的消息将被送去摘要
    CTX_LOW_WATERMARK_RATIO: float = 0.5
    # Redis 回填时从 MongoDB 拉取的历史消息条数上限
    CTX_FALLBACK_HISTORY_LIMIT: int = 20

    # Agentic ReAct 循环
    # ReAct 最大推理迭代次数，防止工具调用产生无限循环
    AGENT_MAX_ITERATIONS: int = 5
    # 工具返回内容的字符截断上限（约 ~1000 token），防止超长结果撑爆后续迭代的上下文水位
    TOOL_RESULT_MAX_CHARS: int = 4000

    # Skill 系统配置
    # 开发期 fixture 根目录：bootstrap_settings.is_dev=True 时 LocalFS 加载器先在这里
    # 找资产，找不到才回退 OSS。生产形态完全不读这个目录，直接走 OssSkillAssetLoader。
    SKILL_ASSETS_CACHE_DIR: str = "dev_fixtures/skill_bundles"
    @property
    def SKILL_ASSETS_CACHE_PATH(self) -> Path:
        path = Path(self.SKILL_ASSETS_CACHE_DIR)
        if path.is_absolute():
            return path
        return (SERVICE_ROOT / path).resolve()
    # OSS 资产本地磁盘缓存目录（运行期管理，GC 自动清理）
    SKILL_OSS_CACHE_DIR: str = "/var/skill_oss_cache"
    # 缓存文件 TTL：mtime 距今超过该秒数 → GC 清理（默认 6 小时）
    SKILL_OSS_CACHE_TTL_SECONDS: int = 6 * 3600
    # GC 扫描周期（秒）
    SKILL_OSS_CACHE_GC_INTERVAL_SECONDS: int = 30 * 60
    # Matcher 每轮给 LLM 暴露的 skill 候选上限（受控披露，防 LLM 误加载）
    SKILL_MATCH_TOP_K: int = 2
    # Skill 元数据缓存 TTL（秒）。用户/Java 端发布的新 Skill 最坏需等 TTL 才被当前副本感知。
    # 过小会增加 Mongo 读压力；过大会让新 Skill 生效滞后。
    # 未来接 Kafka 事件驱动刷新后可放大此值作为兜底轮询。
    SKILL_CACHE_TTL_SECONDS: int = 30

    # 内部 RPC / 服务发现 配置
    # Nacos 服务发现客户端侧负载均衡策略：weighted_random | round_robin | random
    RPC_LB_STRATEGY: Literal["weighted_random", "round_robin", "random"] = "weighted_random"
    # 单次请求超时（秒）
    RPC_DEFAULT_TIMEOUT: float = 5.0
    # 单次调用最多额外重试次数（故障转移跨实例）；真实请求次数 = retries + 1
    RPC_DEFAULT_RETRIES: int = 2
    # ServiceDiscovery 本地缓存兜底 TTL（秒），即便订阅通道断连也会周期性强制 list
    SERVICE_DISCOVERY_CACHE_TTL_SECONDS: float = 30.0


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
        return AppSettings(**(config_dict or {}))
    except Exception as e:
        log_error("Nacos 配置拉取或解析", e)
        raise


settings = load_settings()
