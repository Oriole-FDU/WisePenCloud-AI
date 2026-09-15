import asyncio
import threading
from typing import Literal

import yaml
from common.logger import error, info
from pydantic import BaseModel, ConfigDict

from wisepen_mcp.core.config.nacos import nacos_client_manager


class AppSettings(BaseModel):
    model_config = ConfigDict()

    FROM_SOURCE_SECRET: str = "APISIX-wX0iR6tY"

    RPC_LB_STRATEGY: Literal["weighted_random", "round_robin", "random"] = "weighted_random"
    RPC_DEFAULT_TIMEOUT: float = 5.0
    RPC_DEFAULT_RETRIES: int = 2
    SERVICE_DISCOVERY_CACHE_TTL_SECONDS: float = 30.0

    WEB_SEARCH_API_KEY: str = ""  # 平台默认搜索的 GLM 托管密钥，不接受请求侧覆盖。
    WEB_SEARCH_GLM_BASE_URL: str = "https://open.bigmodel.cn/api/paas/v4"
    WEB_SEARCH_EXA_BASE_URL: str = "https://api.exa.ai"
    WEB_SEARCH_TAVILY_BASE_URL: str = "https://api.tavily.com"
    WEB_SEARCH_ANYSEARCH_BASE_URL: str = "https://api.anysearch.com"
    WEB_SEARCH_BAIDU_QIANFAN_BASE_URL: str = "https://qianfan.baidubce.com"
    WEB_SEARCH_TINYFISH_BASE_URL: str = "https://api.search.tinyfish.ai"
    WEB_SEARCH_FIRECRAWL_BASE_URL: str = "https://api.firecrawl.dev"
    WEB_SEARCH_HTTP_TIMEOUT_SECONDS: float = 15.0
    ZERO_ENTROPY_API_KEY: str = ""
    RERANKER_MODEL: str = "zerank-2"

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
