import asyncio
import threading

import yaml
from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from common.logger import error, info
from sandbox.core.config.nacos import nacos_client_manager


class AppSettings(BaseModel):
    """由 Nacos 提供的沙箱服务业务配置。"""

    model_config = ConfigDict()

    # 与网关约定的内部来源校验 token。
    FROM_SOURCE_SECRET: str

    # 沙箱提供者通过 factory 字符串加载，便于本地/生产替换不同沙箱后端。
    SANDBOX_PROVIDER_FACTORY: str

    # 工作区缓存用于沙箱销毁后的文件恢复；当前本地实现按 tenant + workspace 分目录存储。
    SANDBOX_WORKSPACE_ROOT: str
    SANDBOX_WORKSPACE_CACHE_MAX_FILES: int
    SANDBOX_WORKSPACE_CACHE_MAX_FILE_BYTES: int
    SANDBOX_WORKSPACE_CACHE_MAX_TOTAL_BYTES: int
    SANDBOX_WORKSPACE_CACHE_MANIFEST_NAME: str
    SANDBOX_WORKSPACE_STORE_BACKEND: str
    SANDBOX_MONGO_URL: str
    SANDBOX_MONGO_DATABASE: str

    # Docker worker 运行时配置由 Nacos 统一提供，Provider 不再自行读取环境变量回退。
    SANDBOX_DOCKER_BIN: str
    SANDBOX_DOCKER_HOST: str
    SANDBOX_DOCKER_NETWORK: str
    SANDBOX_AIO_PORT: int
    SANDBOX_VNC_PORT: int
    SANDBOX_REQUEST_TIMEOUT_SECONDS: float
    SANDBOX_EXECUTION_DEFAULT_TIMEOUT_MS: int = 30000
    SANDBOX_EXECUTION_MAX_TIMEOUT_MS: int = 120000
    SANDBOX_EXECUTION_TRANSPORT_GRACE_SECONDS: float = 5.0
    SANDBOX_DOCKER_COMMAND_TIMEOUT_SECONDS: float
    SANDBOX_AIO_HEALTH_TIMEOUT_SECONDS: float
    SANDBOX_AIO_HEALTH_RETRY_INTERVAL_SECONDS: float
    SANDBOX_DOCKER_CREATE_MAX_ATTEMPTS: int
    SANDBOX_DOCKER_CREATE_RETRY_BACKOFF_SECONDS: float
    SANDBOX_AIO_WORKDIR: str
    SANDBOX_CONTAINER_WORKSPACE_ROOT: str
    SANDBOX_CONTAINER_USER: str
    SANDBOX_DOCKER_TTY: bool
    SANDBOX_OWNER_ID: str
    # 本地 Docker/Colima 环境可显式传入 --no-sandbox，生产默认不改变 Chromium 隔离。
    SANDBOX_BROWSER_NO_SANDBOX: str = ""
    SANDBOX_PUBLIC_VNC_URL_TEMPLATE: str
    SANDBOX_PUBLIC_WEBSOCKET_URL_TEMPLATE: str
    SANDBOX_CHECKPOINT_INTERVAL_SECONDS: float
    SANDBOX_VNC_IDLE_TIMEOUT_SECONDS: float
    SANDBOX_VNC_IDLE_CLEANUP_INTERVAL_SECONDS: float

    # 预热容器镜像；Provider 可在 SandboxSpec 为空时回退到该值。
    SANDBOX_IMAGE: str

    # 租约和预热池容量参数。reserve 会额外保持冗余 READY 实例，缓冲并发突刺。
    SANDBOX_LEASE_TTL_SECONDS: int
    SANDBOX_USER_REUSE_ENABLED: bool = True
    SANDBOX_USER_IDLE_TTL_SECONDS: int = 600
    SANDBOX_MAX_USER_BINDINGS: int = 20
    SANDBOX_TARGET_READY: int
    SANDBOX_MIN_READY: int
    SANDBOX_READY_RESERVE: int
    SANDBOX_MAX_CREATE_BATCH: int
    SANDBOX_WARMUP_TIMEOUT_SECONDS: float
    SANDBOX_DESTROY_TIMEOUT_SECONDS: float
    SANDBOX_WARMUP_MAX_RETRIES: int
    SANDBOX_WARMUP_RETRY_BACKOFF_SECONDS: float
    SANDBOX_WARMUP_RETRY_MAX_BACKOFF_SECONDS: float
    SANDBOX_WATCHER_INTERVAL_SECONDS: float
    SANDBOX_LEADER_LEASE_TTL_SECONDS: float
    SANDBOX_LEADER_LEASE_RENEW_INTERVAL_SECONDS: float
    SANDBOX_DESTROY_MAX_RETRIES: int
    SANDBOX_DESTROY_RETRY_BACKOFF_SECONDS: float

    @field_validator("SANDBOX_WORKSPACE_STORE_BACKEND")
    @classmethod
    def validate_store_backend(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in {"local", "mongo"}:
            raise ValueError("SANDBOX_WORKSPACE_STORE_BACKEND 只能是 local 或 mongo")
        return normalized

    @field_validator("SANDBOX_BROWSER_NO_SANDBOX")
    @classmethod
    def normalize_browser_no_sandbox(cls, value: str) -> str:
        return value.strip()

    @model_validator(mode="after")
    def validate_runtime_values(self) -> "AppSettings":
        if self.SANDBOX_AIO_PORT == self.SANDBOX_VNC_PORT:
            raise ValueError("AIO API 和 VNC 容器端口不能相同")
        if not (1 <= self.SANDBOX_AIO_PORT <= 65535):
            raise ValueError("SANDBOX_AIO_PORT 非法")
        if not (1 <= self.SANDBOX_VNC_PORT <= 65535):
            raise ValueError("SANDBOX_VNC_PORT 非法")
        positive = (
            self.SANDBOX_WORKSPACE_CACHE_MAX_FILES,
            self.SANDBOX_WORKSPACE_CACHE_MAX_FILE_BYTES,
            self.SANDBOX_WORKSPACE_CACHE_MAX_TOTAL_BYTES,
            self.SANDBOX_LEASE_TTL_SECONDS,
            self.SANDBOX_USER_IDLE_TTL_SECONDS,
            self.SANDBOX_MAX_USER_BINDINGS,
            self.SANDBOX_MAX_CREATE_BATCH,
            self.SANDBOX_REQUEST_TIMEOUT_SECONDS,
            self.SANDBOX_EXECUTION_DEFAULT_TIMEOUT_MS,
            self.SANDBOX_EXECUTION_MAX_TIMEOUT_MS,
            self.SANDBOX_EXECUTION_TRANSPORT_GRACE_SECONDS,
            self.SANDBOX_DOCKER_COMMAND_TIMEOUT_SECONDS,
            self.SANDBOX_AIO_HEALTH_TIMEOUT_SECONDS,
            self.SANDBOX_AIO_HEALTH_RETRY_INTERVAL_SECONDS,
            self.SANDBOX_DOCKER_CREATE_MAX_ATTEMPTS,
            self.SANDBOX_DOCKER_CREATE_RETRY_BACKOFF_SECONDS,
            self.SANDBOX_WARMUP_TIMEOUT_SECONDS,
            self.SANDBOX_DESTROY_TIMEOUT_SECONDS,
            self.SANDBOX_WARMUP_MAX_RETRIES,
            self.SANDBOX_WARMUP_RETRY_BACKOFF_SECONDS,
            self.SANDBOX_WARMUP_RETRY_MAX_BACKOFF_SECONDS,
            self.SANDBOX_WATCHER_INTERVAL_SECONDS,
            self.SANDBOX_LEADER_LEASE_TTL_SECONDS,
            self.SANDBOX_LEADER_LEASE_RENEW_INTERVAL_SECONDS,
            self.SANDBOX_DESTROY_MAX_RETRIES,
            self.SANDBOX_DESTROY_RETRY_BACKOFF_SECONDS,
            self.SANDBOX_CHECKPOINT_INTERVAL_SECONDS,
            self.SANDBOX_VNC_IDLE_TIMEOUT_SECONDS,
            self.SANDBOX_VNC_IDLE_CLEANUP_INTERVAL_SECONDS,
        )
        if any(value <= 0 for value in positive):
            raise ValueError("沙箱容量、超时和文件限制必须为正数")
        if (
            self.SANDBOX_EXECUTION_DEFAULT_TIMEOUT_MS
            > self.SANDBOX_EXECUTION_MAX_TIMEOUT_MS
        ):
            raise ValueError("沙箱默认执行超时不能大于最大执行超时")
        if (
            self.SANDBOX_LEADER_LEASE_RENEW_INTERVAL_SECONDS
            >= self.SANDBOX_LEADER_LEASE_TTL_SECONDS
        ):
            raise ValueError("leader 租约续期周期必须小于租约 TTL")
        if (
            self.SANDBOX_WARMUP_RETRY_MAX_BACKOFF_SECONDS
            < self.SANDBOX_WARMUP_RETRY_BACKOFF_SECONDS
        ):
            raise ValueError("预热重试最大退避不能小于初始退避")
        required_text = (
            self.FROM_SOURCE_SECRET,
            self.SANDBOX_PROVIDER_FACTORY,
            self.SANDBOX_DOCKER_BIN,
            self.SANDBOX_IMAGE,
            self.SANDBOX_AIO_WORKDIR,
            self.SANDBOX_CONTAINER_WORKSPACE_ROOT,
            self.SANDBOX_CONTAINER_USER,
            self.SANDBOX_OWNER_ID,
        )
        if any(not value.strip() for value in required_text):
            raise ValueError("沙箱生产配置包含空的必填字段")
        return self


def _run_async(coro):
    """在独立事件循环中执行协程，用于配置加载阶段的异步调用。"""
    result, exc = None, None

    def _target():
        nonlocal result, exc
        try:
            result = asyncio.run(coro)
        except Exception as e:
            exc = e

    thread = threading.Thread(target=_target)
    thread.start()
    thread.join()
    if exc:
        raise exc
    return result


def load_settings() -> AppSettings:
    try:
        info("正在拉取 nacos 应用配置。")
        raw_yaml = _run_async(nacos_client_manager.pull_config())
        if not raw_yaml or not raw_yaml.strip():
            raise RuntimeError("nacos 沙箱配置为空")
        config_dict = yaml.safe_load(raw_yaml)
        if not isinstance(config_dict, dict) or not config_dict:
            raise RuntimeError("nacos 沙箱配置必须是非空对象")
        return AppSettings(**config_dict)
    except Exception as e:
        error("拉取 nacos 应用配置失败。", exc=e)
        raise


settings = load_settings()
