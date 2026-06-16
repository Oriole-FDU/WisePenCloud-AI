from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from typing import Any

import httpx

from ..providers.models import ProviderSearchResponse, SearchProviderEndpoint, SearchProviderName


@dataclass(frozen=True, slots=True)
class SearchProviderConfig:
    base_url: str  # provider API base URL
    api_key: str | None = None  # 用户或平台凭证
    source_id: str | None = None  # 区分平台内置源和用户自定义源


class SearchProviderError(RuntimeError):
    """搜索源请求或响应解析失败。"""


class SearchProviderCredentialError(SearchProviderError):
    """搜索源鉴权失败、key 过期或额度耗尽。"""


class SearchProviderNetworkError(SearchProviderError):
    """搜索源网络波动或远端服务暂不可用。"""


SearchResponseMapper = Callable[..., ProviderSearchResponse]


class BaseProviderSearcher:
    """Provider searcher 基类。

    子类声明 provider、request_class、response_mapper，并在初始化时独立构造 headers。
    provider 请求对象负责把自身转换为 HTTP 请求，避免 searcher 关心 payload 形态。
    """

    provider: SearchProviderName
    request_class: type
    response_mapper: SearchResponseMapper

    def __init__(
        self,
        *,
        http_client: httpx.AsyncClient,
        config: SearchProviderConfig,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        if not config.base_url.strip():
            raise ValueError(f"{self.provider} base_url is required.")
        self._http = http_client
        self._config = replace(config, base_url=config.base_url.strip())
        self._headers = dict(headers or {})

    async def search(
        self,
        *,
        query: str,
        endpoint: SearchProviderEndpoint,
        max_results: int,
    ) -> ProviderSearchResponse:
        request = self.request_class(
            query=query,
            endpoint=endpoint,
            max_results=max_results,
        )
        http_request = request.to_http_request()
        data = await self._request_json(
            method=http_request.method,
            url=f"{self._config.base_url.rstrip('/')}/{http_request.path.lstrip('/')}",
            params=http_request.params,
            json=http_request.json,
        )
        response = self.response_mapper(
            data,
            query=query,
            endpoint=endpoint,
            max_results=max_results,
        )
        return (
            response
            if self._config.source_id is None
            else replace(response, source_id=self._config.source_id)
        )

    async def _request_json(
        self,
        *,
        method: str,
        url: str,
        params: dict[str, object] | None,
        json: dict[str, object] | None,
    ) -> dict[str, Any]:
        request: dict[str, Any] = {}
        if self._headers:
            request["headers"] = dict(self._headers)
        if params is not None:
            request["params"] = params
        if json is not None:
            request["json"] = json

        try:
            response = await self._http.request(method, url, **request)
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code
            if status_code in {401, 403, 429}:
                raise SearchProviderCredentialError(
                    f"{self.provider} 凭证不可用或额度不足: HTTP {status_code}"
                ) from exc
            if status_code >= 500:
                raise SearchProviderNetworkError(
                    f"{self.provider} 远端服务暂不可用: HTTP {status_code}"
                ) from exc
            raise SearchProviderError(
                f"{self.provider} 请求失败: HTTP {status_code}"
            ) from exc
        except (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError) as exc:
            raise SearchProviderNetworkError(f"{self.provider} 网络请求失败: {exc}") from exc
        except httpx.HTTPError as exc:
            raise SearchProviderError(f"{self.provider} 请求失败: {exc}") from exc

        try:
            data = response.json()
        except ValueError as exc:
            raise SearchProviderError(f"{self.provider} 响应不是合法 JSON") from exc
        if not isinstance(data, dict):
            raise SearchProviderError(f"{self.provider} 响应不是 JSON object")
        return data
