from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Mapping

import httpx

from chat.application.tools.web.services.web_search.enums import SearcherName
from chat.application.tools.web.services.web_search.errors import SearchProviderError
from chat.application.tools.web.services.web_search.models import SearchResponse, SearchResult
from chat.application.tools.web.services.web_search.searcher.base import WebSearcher
from chat.application.tools.web.services.web_search.utils.http_client import fetch_search_json
from chat.application.tools.web.services.web_search.utils.results import is_valid_result
from common.logger import log_event, log_fail

_WIKIPEDIA_API_PATH = "/w/api.php"
_WIKIPEDIA_SUMMARY_PATH = "/api/rest_v1/page/summary"


class WikipediaSearcher(WebSearcher):
    """English Wikipedia background text searcher."""

    def __init__(
            self,
            client: httpx.AsyncClient,
            *,
            base_url: str = "https://en.wikipedia.org",
            user_agent: str = "WisePenCloud-AI-WebSearch/1.0 (contact: example@",
            max_extract_chars: int = 800,
            concurrency: int = 4,
    ) -> None:
        self._client = client
        self._base_url = base_url.rstrip("/")
        self._api_url = f"{self._base_url}{_WIKIPEDIA_API_PATH}"
        self._summary_base_url = f"{self._base_url}{_WIKIPEDIA_SUMMARY_PATH}"
        self._max_extract_chars = max_extract_chars
        self._semaphore = asyncio.Semaphore(concurrency)
        self._headers = {
            "User-Agent": user_agent,
            "Api-User-Agent": user_agent,
            "Accept": "application/json",
        }

    @property
    def name(self) -> SearcherName:
        """返回组件名称。"""
        return SearcherName.WIKIPEDIA

    async def search(
            self,
            query: str,
            *,
            max_results: int = 3,
    ) -> SearchResponse:
        """纯文本 Grounding 核心链路。"""
        payload = await self._fetch_wikipedia_json_value(
            url=self._api_url,
            params={
                "action": "opensearch",
                "search": query,
                "limit": str(max_results),
                "namespace": "0",
                "redirects": "resolve",
                "format": "json",
            },
            query=query,
            timeout_seconds=5.0,
        )

        if not isinstance(payload, (list, tuple)) or len(payload) < 4:
            log_fail(
                "Wikipedia 外部 payload 长度不足，解包",
                f"expected_len>=4, actual_type={type(payload).__name__}",
                query=query,
            )
            return SearchResponse(
                query=query,
                results=[],
                source=SearcherName.WIKIPEDIA,
            )

        raw_titles = payload[1]
        raw_snippets = payload[2]
        raw_urls = payload[3]

        if (
                not isinstance(raw_titles, list)
                or not isinstance(raw_snippets, list)
                or not isinstance(raw_urls, list)
        ):
            log_event(
                "Wikipedia 外部数据容器类型畸形",
                titles=type(raw_titles).__name__,
                snippets=type(raw_snippets).__name__,
                urls=type(raw_urls).__name__,
                query=query,
            )
            return SearchResponse(
                query=query,
                results=[],
                source=SearcherName.WIKIPEDIA,
            )

        if not (len(raw_titles) == len(raw_snippets) == len(raw_urls)):
            log_event(
                "Wikipedia 外部数据矩阵长度非对称",
                titles_len=len(raw_titles),
                snippets_len=len(raw_snippets),
                urls_len=len(raw_urls),
                query=query,
            )
            return SearchResponse(
                query=query,
                results=[],
                source=SearcherName.WIKIPEDIA,
            )

        tasks = [
            self._build_page_result(
                title=str(title).strip(),
                page_snippet=str(snippet).strip(),
                page_url=str(url).strip(),
                query=query,
            )
            for title, snippet, url in zip(raw_titles, raw_snippets, raw_urls)
            if str(title).strip()
        ]

        raw_results = await asyncio.gather(*tasks)

        results: List[SearchResult] = [
            result
            for result in raw_results
            if isinstance(result, SearchResult) and is_valid_result(result)
        ]

        return SearchResponse(
            query=query,
            results=results[:max_results],
            source=SearcherName.WIKIPEDIA,
        )

    async def _fetch_wikipedia_json_value(
            self,
            *,
            url: str,
            params: Dict[str, str],
            query: str,
            timeout_seconds: float,
    ) -> Any:
        """获取 Wikipedia JSON value。

        Args:
        - url: Wikipedia API URL。
        - params: GET 查询参数。
        - query: 原始查询，仅用于错误追踪。
        - timeout_seconds: 请求超时时间。

        Returns:
        - Wikipedia 返回的任意合法 JSON value。
        """
        timeout = httpx.Timeout(
            timeout=timeout_seconds,
            connect=min(3.0, timeout_seconds),
            read=timeout_seconds,
        )

        try:
            async with self._semaphore:
                response = await self._client.get(
                    url=url,
                    params=params,
                    headers=self._headers,
                    timeout=timeout,
                )

            response.raise_for_status()

            try:
                return response.json()
            except (ValueError, TypeError) as e:
                raise SearchProviderError(
                    provider=SearcherName.WIKIPEDIA.value,
                    status_code=response.status_code,
                    reason=f"response_is_not_valid_json:{str(e)}",
                ) from e

        except httpx.TimeoutException as e:
            raise SearchProviderError(
                provider=SearcherName.WIKIPEDIA.value,
                status_code=0,
                reason=f"timeout:{timeout_seconds}",
            ) from e

        except httpx.HTTPStatusError as e:
            raise SearchProviderError(
                provider=SearcherName.WIKIPEDIA.value,
                status_code=e.response.status_code,
                reason="http_status_error",
            ) from e

        except httpx.RequestError as e:
            raise SearchProviderError(
                provider=SearcherName.WIKIPEDIA.value,
                status_code=0,
                reason=f"connection_failed:{str(e)}",
            ) from e

    async def _build_page_result(
            self,
            *,
            title: str,
            page_snippet: str,
            page_url: str,
            query: str,
    ) -> SearchResult:
        """组合 OpenSearch 基础命中结果与可选 REST Summary 增强摘要。"""
        fallback_result = SearchResult(
            title=title,
            url=page_url,
            snippet=page_snippet,
        )

        encoded_title = title.replace(" ", "_")
        endpoint = f"{self._summary_base_url}/{encoded_title}"

        try:
            summary_data = await fetch_search_json(
                client=self._client,
                url=endpoint,
                params={},
                query=query,
                headers=self._headers,
                timeout_seconds=3.0,
                semaphore=self._semaphore,
                provider_name=SearcherName.WIKIPEDIA.value,
            )
        except SearchProviderError:
            return fallback_result

        extract = str(summary_data.get("extract", "")).strip() or page_snippet
        if len(extract) > self._max_extract_chars:
            extract = extract[:self._max_extract_chars].rstrip()

        canonical_url = ""
        content_urls = summary_data.get("content_urls")
        if isinstance(content_urls, Mapping):
            desktop = content_urls.get("desktop")
            if isinstance(desktop, Mapping):
                canonical_url = str(desktop.get("page", "")).strip()

        return SearchResult(
            title=str(summary_data.get("title", title)).strip(),
            url=canonical_url or page_url,
            snippet=extract,
        )