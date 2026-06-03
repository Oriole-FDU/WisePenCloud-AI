from __future__ import annotations

import os
import threading
import asyncio
from pathlib import Path
import yaml
from pydantic import BaseModel, ConfigDict

from common.core.config.bootstrap_settings import BootstrapSettings
from aio_gateway.nacos import nacos_client_manager
from common.logger import log_event, log_error

SERVICE_ROOT = Path(__file__).resolve().parents[3]


class GatewayBootstrapSettings(BootstrapSettings):
    APP_NAME: str = "WisePen AIO Gateway"
    SERVICE_NAME: str = "wisepen-aio-service"
    SERVICE_PORT: int = 8001


bootstrap_settings = GatewayBootstrapSettings()


class AppSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    FROM_SOURCE_SECRET: str
    AIO_BASE_URL: str = "http://127.0.0.1:8080"

    # 工作域清理配置
    WORKSPACE_CLEANUP_TTL_SECONDS: int = 7 * 24 * 3600   # 7 天未访问 → 清理
    WORKSPACE_CLEANUP_INTERVAL_SECONDS: int = 3600        # 每小时扫描一次


def _load_local() -> dict:
    cfg_path = SERVICE_ROOT / "wisepen-aio-gateway.nacos.yaml"
    raw = cfg_path.read_text(encoding="utf-8")
    cfg = yaml.safe_load(raw) if raw else {}
    if not isinstance(cfg, dict):
        raise ValueError("local config must be a yaml mapping")
    log_event("使用本地配置文件启动（DEV 模式）", file=str(cfg_path))
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
        log_event("从 Nacos 拉取核心业务配置")
        raw_yaml = _run_async(nacos_client_manager.pull_config())
        config_dict = yaml.safe_load(raw_yaml) if raw_yaml else {}
        return AppSettings(**(config_dict or {}))
    except Exception as e:
        log_error("Nacos 配置拉取或解析", e)
        if bootstrap_settings.IS_DEV:
            return AppSettings(**_load_local())
        raise


settings = load_settings()
