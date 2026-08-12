from __future__ import annotations

import asyncio
import threading

import yaml
from common.logger import error, info
from pydantic import BaseModel, ConfigDict

from sandbox.core.config.bootstrap_settings import bootstrap_settings
from sandbox.core.config.nacos import nacos_client_manager


class AppSettings(BaseModel):
    """沙箱池核心运行配置。

    这些配置只覆盖 core 的池容量、warmup、销毁、重试和鉴权参数。应用启动
    前会由 Nacos 提供完整配置。Mongo 配置用于持久化 sandbox/workspace 权威状态，
    Workspace 配置用于受管目录、快照缓存和后台淘汰策略。
    """

    model_config = ConfigDict(extra="forbid")

    # 内部调用鉴权与 Mongo 权威存储配置。
    FROM_SOURCE_SECRET: str
    MONGODB_URL: str
    MONGODB_DB_NAME: str
    REDIS_URL: str

    # sandbox 池容量、warmup、销毁和重试配置。
    SANDBOX_IMAGE: str
    SANDBOX_PROVIDER_ID: str = "default"
    SANDBOX_MAX_USER_BINDINGS: int = 20
    SANDBOX_TARGET_READY: int
    SANDBOX_MIN_READY: int
    SANDBOX_READY_RESERVE: int = 0
    SANDBOX_MAX_CREATE_BATCH: int
    SANDBOX_WARMUP_TIMEOUT_SECONDS: float
    SANDBOX_DESTROY_TIMEOUT_SECONDS: float
    SANDBOX_WARMUP_MAX_RETRIES: int
    SANDBOX_WARMUP_RETRY_BACKOFF_SECONDS: float
    SANDBOX_WARMUP_RETRY_MAX_BACKOFF_SECONDS: float
    SANDBOX_WATCHER_INTERVAL_SECONDS: float
    SANDBOX_DOCKER_ENDPOINT_HOST: str = "127.0.0.1"
    SANDBOX_AIO_HEALTH_TIMEOUT_SECONDS: float = 5.0

    # workspace 受管目录、快照缓存容量和后台淘汰配置。
    SANDBOX_WORKSPACE_ROOT: str = "./data/workspaces"
    SANDBOX_WORKSPACE_CACHE_ROOT: str = "./data/workspace-cache"
    SANDBOX_WORKSPACE_SNAPSHOT_TTL_SECONDS: int = 7 * 24 * 60 * 60
    SANDBOX_WORKSPACE_CACHE_MAX_BYTES: int = 0
    SANDBOX_WORKSPACE_CACHE_HIGH_WATERMARK_RATIO: float = 0.8
    SANDBOX_WORKSPACE_CACHE_TARGET_WATERMARK_RATIO: float = 0.7
    SANDBOX_WORKSPACE_EVICTION_INTERVAL_SECONDS: float = 3600.0


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
    """从 Nacos 拉取 sandbox core 配置并构造 AppSettings。"""

    try:
        # 当前服务启动严格依赖 Nacos 配置；拉取失败直接暴露启动错误。
        info("nacos app config pulling.")
        raw_yaml = _run_async(nacos_client_manager.pull_config())
        config_dict = yaml.safe_load(raw_yaml) if raw_yaml else {}
        return AppSettings(**(config_dict or {}))
    except Exception as e:
        error("nacos app config pull failed.", exc=e)
        raise

settings = load_settings()
