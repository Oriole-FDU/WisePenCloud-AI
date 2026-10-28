"""与 Chat/Sandbox 相同的 Nacos 配置加载入口。"""

from __future__ import annotations

import asyncio
import json
import threading

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

from scholar.core.config.nacos import nacos_client_manager


class AppSettings(BaseModel):
    """业务配置只包含已使用的依赖；凭证不进入业务请求模型。"""

    model_config = ConfigDict(extra="ignore", hide_input_in_errors=True)

    # --- 基础设施 ---------------------------------------------------------
    FROM_SOURCE_SECRET: str
    ELASTICSEARCH_URIS: str
    ELASTICSEARCH_USERNAME: str
    ELASTICSEARCH_PASSWORD: str
    REDIS_URL: str

    # --- 外部 API 凭证（repr=False 防止泄漏到日志） ---
    S2_API_KEY: str = Field(repr=False)
    HF_TOKEN: str | None = Field(default=None, repr=False)
    OPENALEX_API_KEY: str | None = Field(default=None, repr=False)
    UNPAYWALL_EMAIL: str = "jzsun24@m.fudan.edu.cn"

    # --- Embedding 服务 ---
    SCHOLAR_EMBEDDING_TIMEOUT_SECONDS: float = Field(default=2, gt=0)
    SCHOLAR_EMBEDDING_CPU_THREADS: int = Field(default=2, gt=0)
    SCHOLAR_EMBEDDING_MODELS_PATH: str | None = None
    # 本地开发默认值，生产通过 Nacos 覆盖为 Linux 解释器路径。
    SCHOLAR_EMBEDDING_GPU_PYTHON: str = "D:/Rag/.venv/Scripts/python.exe"

    # --- 索引 ---
    SCHOLAR_INDEX_ALIAS: str = "wisepen-scholar-arxiv"

    # --- 检索参数 ---
    # 控制两路召回成本与排序先验；固定文本权重和模型身份随代码发布。
    SCHOLAR_SEARCH_CANDIDATES: int = Field(default=200, gt=0)
    SCHOLAR_SEARCH_NUM_CANDIDATES: int = Field(default=1000, gt=0)
    SCHOLAR_SEARCH_RRF_K: int = Field(default=60, gt=0)
    SCHOLAR_SEARCH_CITATION_WEIGHT: float = Field(default=0.20, ge=0)
    SCHOLAR_SEARCH_INFLUENTIAL_WEIGHT: float = Field(default=0.10, ge=0)

    # --- 缓存 TTL（秒） ---
    SCHOLAR_QUERY_CACHE_TTL_SECONDS: int = Field(default=600, gt=0)
    SCHOLAR_DISCOVERY_CACHE_TTL_SECONDS: int = Field(default=900, gt=0)
    SCHOLAR_PAPER_CACHE_TTL_SECONDS: int = Field(default=86400, gt=0)
    SCHOLAR_NEGATIVE_CACHE_TTL_SECONDS: int = Field(default=3600, gt=0)

    # --- 并发与超时 ---
    SCHOLAR_HF_CONCURRENCY: int = Field(default=4, gt=0)
    SCHOLAR_S2_MAX_QUEUED_AHEAD: int = Field(default=5, ge=0)
    SCHOLAR_REQUEST_TIMEOUT_SECONDS: float = Field(default=25, gt=0)
    SCHOLAR_ETL_REQUEST_TIMEOUT_SECONDS: float = Field(default=120, gt=0)

    @field_validator("ELASTICSEARCH_URIS", mode="before")
    @classmethod
    def serialize_uris(cls, value):
        """允许 Nacos 以列表形式注入，统一序列化为 JSON 字符串。"""
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
    from scholar.core.config.bootstrap_settings import SERVICE_ROOT, bootstrap_settings

    if not bootstrap_settings.NACOS_ENABLED:
        import os

        from dotenv import dotenv_values

        return AppSettings(**{**dotenv_values(SERVICE_ROOT / ".env"), **os.environ})
    return AppSettings(
        **yaml.safe_load(_run_async(nacos_client_manager.pull_config()))
    )


settings = load_settings()
