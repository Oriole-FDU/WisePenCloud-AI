from __future__ import annotations

import asyncio

import httpx

from chat.application.tools.web.services.web_search.enums import SearcherName
from chat.application.tools.web.services.web_search.errors import SearchProviderTransientError
from chat.application.tools.web.services.web_search.models import SearchResponse
from chat.application.tools.web.services.web_search.schemas.brave import (
    BraveSearchRequest,
    map_brave_response,
)
from chat.application.tools.web.services.web_search.searcher.base import WebSearcher
from chat.application.tools.web.services.web_search.utils.http_client import fetch_search_json
from common.logger import log_event

_BRAVE_ENDPOINT = "/res/v1/web/search"


class BraveSearcher(WebSearcher):
    """Brave Search API 搜索器。

    标准 WebSearcher 实现模板（to_params / GET 模式）：
      1. 构建请求实体 → request.to_params() 转为 HTTP 查询参数
      2. 调用 fetch_search_json 发送 GET 请求
      3. 校验响应为 dict
      4. 调用 map_*_response 映射为统一 SearchResponse
      5. 空结果时抛出 SearchProviderTransientError 触发重试

    Args:
        client: httpx 异步客户端（由依赖注入提供）。
        api_key: Brave API 密钥。
        base_url: Brave Search API 基础地址。
        concurrency: 并发请求信号量上限。
    """

    def __init__(
            self,
            client: httpx.AsyncClient,
            *,
            api_key: str,
            base_url: str,
            concurrency: int = 5,
    ) -> None:
        self._request_url = f"{base_url.rstrip('/')}{_BRAVE_ENDPOINT}"
        self._api_key = api_key.strip()
        self._semaphore = asyncio.Semaphore(concurrency)

        self._headers = {
            "X-Subscription-Token": self._api_key,
            "Accept": "application/json",
            "Accept-Encoding": "gzip",
        }

    @property
    def name(self) -> SearcherName:
        return SearcherName.BRAVE

    async def search(
            self,
            query: str,
            *,
            max_results: int = 10,
            timeout_seconds: float = 8.0,
    ) -> SearchResponse:
        request = BraveSearchRequest(query=query, count=max_results)

        data = await fetch_search_json(
            client=self._client,
            url=self._request_url,
            params=request.to_params(),
            query=query,
            timeout_seconds=timeout_seconds,
            semaphore=self._semaphore,
            provider_name=self.name.value,
            headers=self._headers,
        )

        if not isinstance(data, dict):
            log_event(
                "Brave 返回了畸形非字典结构",
                data_type=type(data).__name__,
                query=query
            )

            return SearchResponse(
                query=query,
                results=[],
                source=self.name,
            )

        response = map_brave_response(
            data,
            query=query,
            max_results=max_results,
        )

        # 空结果抛出可重试异常（上游无结果，非调用错误）
        if not response.results:
            raise SearchProviderTransientError(
                provider=self.name.value,
                queries=[query],
                reason="Brave empty_result returned from upstream source",
            )

        return response