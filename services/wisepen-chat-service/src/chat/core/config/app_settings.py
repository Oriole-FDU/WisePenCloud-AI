import os
import yaml
import asyncio
from typing import List
from pydantic import BaseModel

from chat.core.config.bootstrap_settings import bootstrap_settings
from common.cloud.nacos_client import nacos_client_manager
from common.logger import log_event, log_error

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV_FILE_PATH = os.path.join(BASE_DIR, ".env")

# 全量应用配置, 必须由Nacos提供，漏配启动报错
class AppSettings(BaseModel):
    APP_NAME: str
    SERVICE_NAME: str
    SERVICE_HOST: str
    SERVICE_PORT: int
    DEV: bool
    LOG_LEVEL: str

    # LLM 网关配置
    LLM_BASE_URL: str
    LLM_API_KEY: str

    DEFAULT_MODEL: str = "gpt-4o"

    # Kafka 配置
    KAFKA_BOOTSTRAP_SERVERS: str = "wisepen-dev-server:9094"
    KAFKA_TOPIC: str = "wisepen-user-token-calc-group-topic"
    KAFKA_HEADER_TYPE_ID: str = "com.oriole.wisepen.user.domain.entity.TokenCalculateMessage"
    
    # Memory 使用的模型
    MEMORY_LLM_MODEL: str = "gpt-4o"
    MEMORY_EMBEDDING_MODEL: str = "text-embedding-3-large"
    MEMORY_RERANKER_ZE_MODEL: str = "zerank-1"
    ZERO_ENTROPY_API_KEY: str

    # 摘要压缩使用的轻量级模型（调用成本低、速度快）
    SUMMARY_MODEL: str = "openai/gemini-3-flash-preview"

    # 安全配置：与 APISIX 网关约定的防绕过 Token
    FROM_SOURCE_SECRET: str

    # Redis
    REDIS_URL: str
    # MongoDB
    MONGODB_URL: str
    MONGODB_DB_NAME: str
    # Qdrant (Mem0 长期语义记忆向量存储)
    QDRANT_HOST: str
    QDRANT_PORT: int = 6333
    QDRANT_PASSWORD: str

    # Token 动态滑动窗口 + 双水位压缩配置
    # 模型上下文窗口总大小（token 数），默认对齐 gpt-4o 的 128k 上下文 128000
    CTX_TOKEN_LIMIT: int = 900
    # 高水位线（触发阈值）：上下文累计 Token 达到此比例时触发摘要压缩
    CTX_HIGH_WATERMARK_RATIO: float = 0.8
    # 低水位线（安全退役线）：切分时按 Token 保留此比例以内的最新明细
    # 最老的 (HIGH - LOW) 比例的 Token 对应的消息将被送去摘要
    CTX_LOW_WATERMARK_RATIO: float = 0.5
    # Redis 回填时从 MongoDB 拉取的历史消息条数上限
    CTX_FALLBACK_HISTORY_LIMIT: int = 20

    # Agentic ReAct 循环配置
    # ReAct 最大推理迭代次数，防止工具调用产生无限循环
    AGENT_MAX_ITERATIONS: int = 5
    # 工具返回内容的字符截断上限（约 ~1000 token），防止超长结果撑爆后续迭代的上下文水位
    TOOL_RESULT_MAX_CHARS: int = 4000


def load_settings() -> AppSettings:
    try:
        log_event("从 Nacos 拉取核心业务配置")
        raw_yaml = asyncio.run(nacos_client_manager.pull_config())
        config_dict = yaml.safe_load(raw_yaml) if raw_yaml else {}
        full_config = {**bootstrap_settings.model_dump(), **config_dict}
        return AppSettings(**full_config)
    except Exception as e:
        log_error("Nacos 配置拉取或解析", e)
        raise

settings = load_settings()