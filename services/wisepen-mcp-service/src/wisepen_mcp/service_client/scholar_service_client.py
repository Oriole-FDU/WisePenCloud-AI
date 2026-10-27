from __future__ import annotations

import asyncio
from datetime import date
from typing import Any

from common.http.rpc_client import RpcClient

_SCHOLAR_SERVICE_NAME = "wisepen-scholar-service"
_SEARCH_PAPERS_PATH = "/scholar/papers/search"
_LOOKUP_PAPERS_PATH = "/scholar/papers/lookup"
_DISCOVER_PAPERS_PATH = "/scholar/papers/discover"


class ScholarClient:
    """将 MCP 论文请求传给 Scholar；超时同时限制整次调用与单次 RPC 尝试。"""

    def __init__(
        self,
        rpc: RpcClient,
        *,
        service_name: str = _SCHOLAR_SERVICE_NAME,
        timeout: float = 30.0,
    ) -> None:
        self._rpc = rpc
        self._service_name = service_name
        self._timeout = timeout

    async def search_papers(
        self,
        *,
        query: str,
        field: str,
        published_after: date | None,
        published_before: date | None,
        limit: int,
        source_scope: str,
        openalex_api_key: str | None = None,
    ) -> dict[str, Any]:
        # MCP 用 date 生成日期 Schema；HTTP JSON 使用 Scholar 约定的 ISO 日期字符串。
        async with asyncio.timeout(self._timeout):
            return await self._rpc.post(
                self._service_name,
                _SEARCH_PAPERS_PATH,
                json={
                    "query": query,
                    "field": field,
                    "published_after": published_after.isoformat()
                    if published_after is not None
                    else None,
                    "published_before": published_before.isoformat()
                    if published_before is not None
                    else None,
                    "limit": limit,
                    "source_scope": source_scope,
                },
                timeout=self._timeout,
                **(
                    {"headers": {"X-OpenAlex-Api-Key": openalex_api_key}}
                    if openalex_api_key
                    else {}
                ),
            )

    async def lookup_papers(
        self,
        *,
        identifiers: list[str],
        purpose: str,
        field: str = "general",
        openalex_api_key: str | None = None,
    ) -> dict[str, Any]:
        async with asyncio.timeout(self._timeout):
            return await self._rpc.post(
                self._service_name,
                _LOOKUP_PAPERS_PATH,
                json={
                    "identifiers": identifiers,
                    "purpose": purpose,
                    "field": field,
                },
                timeout=self._timeout,
                **(
                    {"headers": {"X-OpenAlex-Api-Key": openalex_api_key}}
                    if openalex_api_key
                    else {}
                ),
            )

    async def discover_papers(
        self,
        *,
        field: str,
        period: str,
        sort: str,
        limit: int,
    ) -> dict[str, Any]:
        async with asyncio.timeout(self._timeout):
            return await self._rpc.post(
                self._service_name,
                _DISCOVER_PAPERS_PATH,
                json={
                    "field": field,
                    "period": period,
                    "sort": sort,
                    "limit": limit,
                },
                timeout=self._timeout,
            )


__all__ = ["ScholarClient"]
