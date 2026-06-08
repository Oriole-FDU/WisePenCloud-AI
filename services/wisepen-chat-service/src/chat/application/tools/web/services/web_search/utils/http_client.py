import asyncio
from typing import Any, Dict, Optional

import httpx

from chat.application.tools.web.services.web_search.errors import (
    SearchProviderError,
    SearchRateLimitError,
    SearchTimeoutError,
)


async def fetch_search_json(
    client: httpx.AsyncClient,
    url: str,
    *,
    query: str,
    timeout_seconds: float,
    semaphore: asyncio.Semaphore,
    provider_name: str,
    headers: Optional[Dict[str, str]] = None,
    params: Optional[Dict[str, Any]] = None,
    payload: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    异步执行搜索引擎 HTTP 请求并返回 JSON 响应。

    Args:
    - client: httpx 异步客户端实例。
    - url: 搜索引擎 API 端点 URL。
    - query: 原始搜索查询，仅用于异常信息携带。
    - timeout_seconds: 整体超时秒数。
    - semaphore: 并发控制信号量。
    - provider_name: 搜索引擎名称，用于错误标记。
    - headers: 可选的自定义 HTTP 头部。
    - params: GET 请求查询参数，与 payload 二选一。
    - payload: POST 请求 JSON 体，与 params 二选一。

    Returns:
    - 搜索引擎返回的 JSON 字典。

    Raises:
    - ValueError: params 和 payload 同时传入或同时缺失。
    - SearchTimeoutError: 请求超时。
    - SearchRateLimitError: 429 频率限制。
    - SearchProviderError: 其他 HTTP / 网络错误。
    """
    if params is not None and payload is not None:
        raise ValueError("params and payload are mutually exclusive")
    if params is None and payload is None:
        raise ValueError("params or payload is required")

    timeout = httpx.Timeout(
        timeout=timeout_seconds,
        connect=min(3.0, timeout_seconds),
        read=timeout_seconds,
    )

    try:
        async with semaphore:
            if params is not None:
                response = await client.get(
                    url=url,
                    params=params,
                    headers=headers,
                    timeout=timeout,
                )
            else:
                response = await client.post(
                    url=url,
                    json=payload,
                    headers=headers,
                    timeout=timeout,
                )

        if response.is_redirect:
            raise SearchProviderError(
                provider=provider_name,
                status_code=response.status_code,
                reason=f"unexpected_redirect_to:{response.headers.get('location', '')}",
            )

        response.raise_for_status()

        try:
            data = response.json()
        except (ValueError, TypeError) as e:
            raise SearchProviderError(
                provider=provider_name,
                status_code=response.status_code,
                reason=f"response_is_not_valid_json:{str(e)}",
            ) from e

        if not isinstance(data, dict):
            raise SearchProviderError(
                provider=provider_name,
                status_code=response.status_code,
                reason="response_json_is_not_object",
            )

        return data

    except httpx.TimeoutException as e:
        raise SearchTimeoutError(
            provider=provider_name,
            queries=[query],
            timeout=timeout_seconds,
        ) from e

    except httpx.HTTPStatusError as e:
        status_code = e.response.status_code

        if status_code == 429:
            try:
                retry_after = int(e.response.headers.get("retry-after", "0"))
            except ValueError:
                retry_after = 0

            raise SearchRateLimitError(
                provider=provider_name,
                retry_after=retry_after,
            ) from e

        raise SearchProviderError(
            provider=provider_name,
            status_code=status_code,
            reason="http_status_error",
        ) from e

    except httpx.RequestError as e:
        raise SearchProviderError(
            provider=provider_name,
            status_code=0,
            reason=f"connection_failed:{str(e)}",
        ) from e