from __future__ import annotations

import os
import threading
import asyncio
from pathlib import Path
import yaml
from pydantic import BaseModel, ConfigDict

from sandbox.gateway.bootstrap import bootstrap_settings
from sandbox.gateway.nacos import nacos_client_manager
from common.logger import info, error

SERVICE_ROOT = Path(__file__).resolve().parents[2]


class AppSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    FROM_SOURCE_SECRET: str

    # 容器队列配置
    WORKER_IMAGE: str = "ghcr.io/agent-infra/sandbox:latest"
    WORKER_MIN_IDLE: int = 2
    WORKER_MAX_TOTAL: int = 8
    WORKER_DIRTY_TTL: int = 60
    AIO_WORKSPACE_CACHE_DIR: str = "/workspaces"


def _load_local() -> dict:
    cfg_path = SERVICE_ROOT / "wisepen-aio-gateway.nacos.yaml"
    raw = cfg_path.read_text(encoding="utf-8")
    cfg = yaml.safe_load(raw) if raw else {}
    if not isinstance(cfg, dict):
        raise ValueError("local config must be a yaml mapping")
    info("使用本地配置文件启动（DEV 模式）", file=str(cfg_path))
    return cfg


def _run_async(coro):
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
    use_nacos = str(os.getenv("CHAT_USE_NACOS") or "").strip().lower() in ("1", "true", "yes")
    if bootstrap_settings.IS_DEV and not use_nacos:
        return AppSettings(**_load_local())

    try:
        info("从 Nacos 拉取核心业务配置")
        raw_yaml = _run_async(nacos_client_manager.pull_config())
        config_dict = yaml.safe_load(raw_yaml) if raw_yaml else {}
        return AppSettings(**(config_dict or {}))
    except Exception as e:
        error("Nacos 配置拉取或解析", exc=e)
        if bootstrap_settings.IS_DEV:
            return AppSettings(**_load_local())
        raise


settings = load_settings()
