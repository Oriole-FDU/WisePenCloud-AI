"""与 Chat/Sandbox 相同的 Nacos 配置加载入口。"""

import asyncio
import json
import threading

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

from scholar.core.config.nacos import nacos_client_manager


class AppSettings(BaseModel):
    """业务配置只包含已经使用的依赖；凭证不进入业务请求模型。"""

    model_config = ConfigDict(extra="ignore", hide_input_in_errors=True)
    FROM_SOURCE_SECRET: str
    ELASTICSEARCH_URIS: str
    ELASTICSEARCH_USERNAME: str
    ELASTICSEARCH_PASSWORD: str
    REDIS_URL: str
    S2_API_KEY: str = Field(repr=False)
    HF_TOKEN: str | None = Field(default=None, repr=False)
    SCHOLAR_INDEX_ALIAS: str = "wisepen-scholar-arxiv"
    SCHOLAR_QUERY_CACHE_TTL_SECONDS: int = Field(default=600, gt=0)
    SCHOLAR_DISCOVERY_CACHE_TTL_SECONDS: int = Field(default=900, gt=0)
    SCHOLAR_PAPER_CACHE_TTL_SECONDS: int = Field(default=86400, gt=0)
    SCHOLAR_NEGATIVE_CACHE_TTL_SECONDS: int = Field(default=3600, gt=0)
    SCHOLAR_HF_CONCURRENCY: int = Field(default=4, gt=0)
    SCHOLAR_REQUEST_TIMEOUT_SECONDS: float = Field(default=25, gt=0)
    SCHOLAR_ETL_REQUEST_TIMEOUT_SECONDS: float = Field(default=120, gt=0)

    @field_validator("ELASTICSEARCH_URIS", mode="before")
    @classmethod
    def serialize_uris(cls, value):
        return json.dumps(value) if isinstance(value, list) else value


def _run_async(coroutine):
    """在独立线程事件循环里拉取配置并传回异常，与现有服务一致。"""
    result, caught = None, None

    def run():
        nonlocal result, caught
        try:
            result = asyncio.run(coroutine)
        except Exception as exc:  # noqa: BLE001 - 跨线程传回原异常，不吞掉失败。
            caught = exc

    thread = threading.Thread(target=run)
    thread.start()
    thread.join()
    if caught is not None:
        raise caught
    return result


def load_settings() -> AppSettings:
    return AppSettings(**yaml.safe_load(_run_async(nacos_client_manager.pull_config())))


settings = load_settings()
