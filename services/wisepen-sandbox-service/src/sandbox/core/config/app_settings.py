import asyncio
import threading

import yaml
from pydantic import BaseModel, ConfigDict

from common.logger import error, info
from sandbox.core.config.nacos import nacos_client_manager


class AppSettings(BaseModel):
    """由 Nacos 提供的沙箱服务业务配置。"""

    model_config = ConfigDict()

    # 与网关约定的内部来源校验 token。
    FROM_SOURCE_SECRET: str = "APISIX-wX0iR6tY"

    # 沙箱提供者通过 factory 字符串加载，便于本地/生产替换不同沙箱后端。
    SANDBOX_PROVIDER_FACTORY: str = "sandbox.core.providers.aio_adapter.provider:AioSandboxProvider"

    # 工作区缓存用于沙箱销毁后的文件恢复；当前本地实现按 tenant + workspace 分目录存储。
    SANDBOX_WORKSPACE_ROOT: str = "/tmp/wisepen-workspaces"
    SANDBOX_WORKSPACE_CACHE_MAX_FILES: int = 2000
    SANDBOX_WORKSPACE_CACHE_MAX_FILE_BYTES: int = 2 * 1024 * 1024
    SANDBOX_WORKSPACE_CACHE_MAX_TOTAL_BYTES: int = 64 * 1024 * 1024
    SANDBOX_WORKSPACE_CACHE_MANIFEST_NAME: str = ".wisepen-workspace-manifest.json"

    # 预热容器镜像；Provider 可在 SandboxSpec 为空时回退到该值。
    SANDBOX_IMAGE: str = "ghcr.io/agent-infra/sandbox:latest"

    # 租约和预热池容量参数。reserve 会额外保持冗余 READY 实例，缓冲并发突刺。
    SANDBOX_LEASE_TTL_SECONDS: int = 1800
    SANDBOX_TARGET_READY: int = 2
    SANDBOX_MIN_READY: int = 1
    SANDBOX_READY_RESERVE: int = 0
    SANDBOX_MAX_CREATE_BATCH: int = 2
    SANDBOX_WARMUP_TIMEOUT_SECONDS: float = 60.0
    SANDBOX_DESTROY_TIMEOUT_SECONDS: float = 60.0
    SANDBOX_WARMUP_MAX_RETRIES: int = 3


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
        config_dict = yaml.safe_load(raw_yaml) if raw_yaml else {}
        return AppSettings(**(config_dict or {}))
    except Exception as e:
        error("拉取 nacos 应用配置失败。", exc=e)
        raise


settings = load_settings()
