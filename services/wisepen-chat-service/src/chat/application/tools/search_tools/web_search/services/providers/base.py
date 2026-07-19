from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any

import httpx

from .core.errors import (
    SearchProviderCredentialError,
    SearchProviderError,
    SearchProviderNetworkError,
)
from .core.models import (
    ProviderSearchRequest,
    ProviderSearchResponse,
    SearchProviderName,
)
from .core.protocols import ProviderSearcher


@dataclass(frozen=True, slots=True)
class SearchProviderConfig:
    base_url: str
    api_key: str | None = None


class BaseProviderSearcher(ProviderSearcher):
    """HTTP 搜索源基类，子类只声明请求契约、解析器和鉴权头。"""

    provider: SearchProviderName | None
    request_class: type

    @staticmethod
    def map_response(
        data: dict[str, Any],
        *,
        query: str,
        max_results: int,
    ) -> ProviderSearchResponse:
        raise NotImplementedError

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
        self._config = replace(
            config,
            base_url=config.base_url.strip(),
        )
        self._headers = dict(headers or {})

    async def search_web(
        self,
        *,
        query: str,
        max_results: int,
    ) -> ProviderSearchResponse:
        return await self._execute_request(
            request=self.request_class(
                query=query,
                max_results=max_results,
            ),
            query=query,
            max_results=max_results,
        )

    async def _execute_request(
        self,
        *,
        request: ProviderSearchRequest,
        query: str,
        max_results: int,
    ) -> ProviderSearchResponse:
        http_request = request.to_http_request()

        data = await self._request_json(
            method=http_request.method,
            url=(
                f"{self._config.base_url.rstrip('/')}/{http_request.path.lstrip('/')}"
            ),
            params=http_request.params,
            json=http_request.json,
        )

        return self.map_response(
            data,
            query=query,
            max_results=max_results,
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
            request["headers"] = self._headers
        if params is not None:
            request["params"] = params
        if json is not None:
            request["json"] = json

        try:
            response = await self._http.request(
                method,
                url,
                **request,
            )
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

        except (
            httpx.TimeoutException,
            httpx.NetworkError,
            httpx.RemoteProtocolError,
        ) as exc:
            raise SearchProviderNetworkError(
                f"{self.provider} 网络请求失败: {exc}"
            ) from exc

        except httpx.HTTPError as exc:
            raise SearchProviderError(f"{self.provider} 请求失败: {exc}") from exc

        try:
            data = response.json()
        except ValueError as exc:
            raise SearchProviderError(f"{self.provider} 响应不是合法 JSON") from exc

        if not isinstance(data, dict):
            raise SearchProviderError(f"{self.provider} 响应不是 JSON object")

        return data
