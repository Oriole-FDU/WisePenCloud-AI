from __future__ import annotations

import asyncio
import threading
from typing import Any

import yaml
from common.logger import error, info
from pydantic import BaseModel, ConfigDict, Field

from sandbox.core.config.nacos import nacos_client_manager
from sandbox.domain.interfaces import SandboxProviderType


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
    SANDBOX_ACTIVE_PROVIDER_ID: SandboxProviderType
    SANDBOX_PROVIDERS: dict[SandboxProviderType, dict[str, Any]]
    SANDBOX_TARGET_READY: int

    # 预热超时时间
    SANDBOX_WARMUP_TIMEOUT_SECONDS: float
    # 销毁超时时间
    SANDBOX_DESTROY_TIMEOUT_SECONDS: float
    # 预热最大尝试次数
    SANDBOX_WARMUP_MAX_RETRIES: int

    # 状态检查时间间隔
    SANDBOX_WATCHER_INTERVAL_SECONDS: float

    # workspace 容器目录、快照缓存容量和后台淘汰配置。
    SANDBOX_CONTAINER_WORKSPACE_ROOT: str = "./data/workspaces"
    SANDBOX_WORKSPACE_CACHE_ROOT: str = "./data/workspace-cache"
    SANDBOX_WORKSPACE_SNAPSHOT_TTL_SECONDS: int = 7 * 24 * 60 * 60
    SANDBOX_WORKSPACE_CACHE_MAX_BYTES: int = 0
    SANDBOX_WORKSPACE_CACHE_HIGH_WATERMARK_RATIO: float = 0.8
    SANDBOX_WORKSPACE_CACHE_TARGET_WATERMARK_RATIO: float = 0.7
    SANDBOX_WORKSPACE_EVICTION_INTERVAL_SECONDS: float = 3600.0

    # 空闲工作区自动回收配置。
    SANDBOX_WORKSPACE_IDLE_TIMEOUT_SECONDS: int = Field(default=900, gt=0)
    SANDBOX_WORKSPACE_RECLAIM_INTERVAL_SECONDS: float = Field(default=60.0, gt=0)
    SANDBOX_WORKSPACE_CACHE_RETRY_COUNT: int = Field(default=3, ge=1, le=3)
    SANDBOX_WORKSPACE_CACHE_RETRY_BACKOFF_SECONDS: float = Field(default=1.0, ge=0)
    SANDBOX_WORKSPACE_RECLAIM_BATCH_SIZE: int = Field(default=100, ge=1)


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
