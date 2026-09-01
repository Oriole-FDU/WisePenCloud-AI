"""由 Nacos 加载的 P0 运行配置。"""

import asyncio
import threading

import yaml
from common.logger import error, info
from pydantic import BaseModel, ConfigDict

from .nacos import nacos_client_manager


class AppSettings(BaseModel):
    """P0 仅声明 Mongo、资源来源和网关安全真正消费的配置。"""

    model_config = ConfigDict(extra="ignore")

    MONGODB_URL: str
    MONGODB_DB_NAME: str
    RESOURCE_MONGODB_DB_NAME: str | None = None
    FROM_SOURCE_SECRET: str = "APISIX-wX0iR6tY"

    @property
    def resource_mongodb_db_name(self) -> str:
        return self.RESOURCE_MONGODB_DB_NAME or self.MONGODB_DB_NAME


def _run_async(coroutine):
    """在独立线程运行 Nacos 协程，兼容 uvicorn 已创建事件循环的启动路径。"""
    result = None
    caught: Exception | None = None

    def run() -> None:
        nonlocal result, caught
        try:
            result = asyncio.run(coroutine)
        except Exception as exc:  # noqa: BLE001  # pragma: no cover - 需将任意协程失败送回启动线程
            caught = exc

    thread = threading.Thread(target=run)
    thread.start()
    thread.join()
    if caught is not None:
        raise caught
    return result


def load_settings() -> AppSettings:
    try:
        info("nacos app config pulling.")
        raw_yaml = _run_async(nacos_client_manager.pull_config())
        return AppSettings(**(yaml.safe_load(raw_yaml) if raw_yaml else {}))
    except Exception as exc:
        error("nacos app config pull failed.", exc=exc)
        raise


settings = load_settings()
