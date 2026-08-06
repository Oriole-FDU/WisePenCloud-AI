import asyncio
import threading
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict

from common.logger import error, info
from wisepen_mcp.core.config.nacos import nacos_client_manager


class AppSettings(BaseModel):
    model_config = ConfigDict()

    FROM_SOURCE_SECRET: str = "APISIX-wX0iR6tY"

    RPC_LB_STRATEGY: Literal["weighted_random", "round_robin", "random"] = (
        "weighted_random"
    )
    RPC_DEFAULT_TIMEOUT: float = 5.0
    RPC_DEFAULT_RETRIES: int = 2
    SERVICE_DISCOVERY_CACHE_TTL_SECONDS: float = 30.0

    WEB_SEARCH_FOURGET_BASE_URL: str = "http://127.0.0.1:8088"
    WEB_SEARCH_EXA_BASE_URL: str = "https://api.exa.ai"
    WEB_SEARCH_TAVILY_BASE_URL: str = "https://api.tavily.com"
    WEB_SEARCH_ANYSEARCH_BASE_URL: str = "https://api.anysearch.com"
    WEB_SEARCH_BAIDU_QIANFAN_BASE_URL: str = "https://qianfan.baidubce.com"
    WEB_SEARCH_TINYFISH_BASE_URL: str = "https://api.search.tinyfish.ai"
    WEB_SEARCH_FIRECRAWL_BASE_URL: str = "https://api.firecrawl.dev"
    WEB_SEARCH_HTTP_TIMEOUT_SECONDS: float = 15.0
    ZERO_ENTROPY_API_KEY: str = ""
    RERANKER_MODEL: str = "zerank-2"

    # RAG 已有 page/section 等稳定结构锚点；这里只决定正文是否可以直接进入
    # visible result。默认值和 chat 侧 tool content read 窗口保持一致，超过后
    # 才交给 ToolReturn.cacheable_texts，让 chat-service 提供后续 range 续读。
    RAG_DIRECT_TEXT_WINDOW_CHAR_BUDGET: int = 24_000
    RAG_DIRECT_TEXT_TOTAL_CHAR_BUDGET: int = 48_000


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
    try:
        info("nacos app config pulling.")
        raw_yaml = _run_async(nacos_client_manager.pull_config())
        config_dict = yaml.safe_load(raw_yaml) if raw_yaml else {}
        return AppSettings(**(config_dict or {}))
    except Exception as e:
        error("nacos app config pull failed.", exc=e)
        raise


settings = load_settings()
