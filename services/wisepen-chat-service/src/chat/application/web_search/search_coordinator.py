from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable, List, Optional, Set, Tuple

from chat.application.web_search.cache import (
    SearchCache,
    make_search_cache_key,
)
from chat.application.web_search.models import SearchResponse
from chat.application.web_search.searcher.duckduckgo_searcher import (
    DuckDuckGoBufferSearcher,
)
from chat.application.web_search.searcher.searxng_searcher import SearXNGSearcher
from chat.application.web_search.searcher.tavily_searcher import TavilySearcher
from chat.core.config.app_settings import settings
from common.logger import log_fail, log_ok

__all__ = [
    "SearchStage",
    "SearchCoordinator",
    "create_search_coordinator",
]

SearchStageFunc = Callable[..., Awaitable[Optional[SearchResponse]]]


@dataclass(frozen=True, slots=True)
class SearchStage:
    name: str
    handler: SearchStageFunc
    cacheable: bool = True


class SearchCoordinator:
    """联网搜索调度器：Fresh Cache + 显式降级链"""

    def __init__(
        self,
        *,
        cache: SearchCache,
        searxng_searcher: SearXNGSearcher,
        duckduckgo_searcher: DuckDuckGoBufferSearcher,
        tavily_searcher: TavilySearcher,
        continue_on_empty: bool = True,
        disabled_stages: Optional[Set[str]] = None,
    ) -> None:
        self._cache = cache
        self._searxng = searxng_searcher
        self._duckduckgo = duckduckgo_searcher
        self._tavily = tavily_searcher
        self._continue_on_empty = continue_on_empty
        self._disabled_stages = disabled_stages or set()

        self._chain: Tuple[SearchStage, ...] = (
            SearchStage("searxng", self._search_searxng, cacheable=True),
            SearchStage("duckduckgo", self._search_duckduckgo, cacheable=True),
            SearchStage("stale_cache", self._search_stale_cache, cacheable=False),
            SearchStage("tavily", self._search_tavily, cacheable=True),
        )

    async def search(
        self,
        query: str,
        *,
        max_results: int = 5,
        with_images: bool = False,
        freshness_required: bool = False,
    ) -> Optional[SearchResponse]:
        key = make_search_cache_key(
            query=query,
            max_results=max_results,
            with_images=with_images,
        )

        fresh = await self._cache.get_fresh(key)
        if fresh is not None:
            log_ok(
                "联网搜索",
                stage="fresh_cache",
                query=query,
                max_results=max_results,
                with_images=with_images,
                results=len(fresh.results),
                images=len(fresh.images),
            )
            return with_source(fresh, "fresh_cache")

        last_empty: Optional[SearchResponse] = None
        failures: List[str] = []

        for stage in self._chain:
            if stage.name in self._disabled_stages:
                failures.append(f"{stage.name}: disabled_by_test")

                log_fail(
                    "联网搜索跳过",
                    "测试注入：stage 被禁用，触发降级",
                    stage=stage.name,
                    query=query,
                    max_results=max_results,
                    with_images=with_images,
                )
                continue

            if stage.name == "stale_cache" and freshness_required:
                failures.append("stale_cache: skipped_for_freshness_required")

                log_fail(
                    "联网搜索跳过",
                    "freshness_required=True，跳过 stale cache",
                    stage=stage.name,
                    query=query,
                    max_results=max_results,
                    with_images=with_images,
                )
                continue

            try:
                response = await stage.handler(
                    query=query,
                    max_results=max_results,
                    with_images=with_images,
                )

            except Exception as e:
                failures.append(f"{stage.name}: {type(e).__name__}: {e}")

                log_fail(
                    "联网搜索",
                    e,
                    stage=stage.name,
                    query=query,
                    max_results=max_results,
                    with_images=with_images,
                )
                continue

            if response is None:
                failures.append(f"{stage.name}: returned_none")

                log_fail(
                    "联网搜索",
                    "stage 返回 None，触发降级",
                    stage=stage.name,
                    query=query,
                    max_results=max_results,
                    with_images=with_images,
                )
                continue

            if not has_content(response):
                last_empty = response
                failures.append(f"{stage.name}: empty_result")

                log_fail(
                    "联网搜索",
                    "搜索结果为空，触发降级",
                    stage=stage.name,
                    query=query,
                    max_results=max_results,
                    with_images=with_images,
                    results=len(response.results),
                    images=len(response.images),
                    has_answer=bool(response.answer),
                    source=response.source,
                )

                if self._continue_on_empty:
                    continue

                return with_source(response, stage.name)

            response = with_source(response, stage.name)

            if stage.cacheable:
                await self._cache.set(key, response)

            log_ok(
                "联网搜索",
                stage=stage.name,
                query=query,
                max_results=max_results,
                with_images=with_images,
                results=len(response.results),
                images=len(response.images),
            )

            return response

        log_fail(
            "联网搜索",
            "所有搜索阶段均失败",
            query=query,
            max_results=max_results,
            with_images=with_images,
            freshness_required=freshness_required,
            failures=failures,
        )

        return last_empty

    async def _search_searxng(
        self,
        *,
        query: str,
        max_results: int,
        with_images: bool,
    ) -> Optional[SearchResponse]:
        return await self._searxng.search(
            query=query,
            max_results=max_results,
            with_images=with_images,
        )

    async def _search_duckduckgo(
        self,
        *,
        query: str,
        max_results: int,
        with_images: bool,
    ) -> Optional[SearchResponse]:
        return await self._duckduckgo.search(
            query=query,
            max_results=max_results,
            with_images=with_images,
        )

    async def _search_stale_cache(
        self,
        *,
        query: str,
        max_results: int,
        with_images: bool,
    ) -> Optional[SearchResponse]:
        key = make_search_cache_key(
            query=query,
            max_results=max_results,
            with_images=with_images,
        )

        return await self._cache.get_stale(key)

    async def _search_tavily(
        self,
        *,
        query: str,
        max_results: int,
        with_images: bool,
    ) -> Optional[SearchResponse]:
        return await self._tavily.search(
            query=query,
            max_results=max_results,
            with_images=with_images,
        )


def has_content(response: SearchResponse) -> bool:
    return bool(response.answer or response.results or response.images)


def with_source(response: SearchResponse, source: str) -> SearchResponse:
    return SearchResponse(
        query=response.query,
        results=response.results,
        answer=response.answer,
        images=response.images,
        source=source,
    )


def create_search_coordinator() -> SearchCoordinator:
    cache = SearchCache(
        fresh_ttl=settings.WEB_SEARCH_FRESH_CACHE_TTL,
        stale_ttl=settings.WEB_SEARCH_STALE_CACHE_TTL,
        maxsize=settings.WEB_SEARCH_CACHE_MAXSIZE,
    )

    return SearchCoordinator(
        cache=cache,
        searxng_searcher=SearXNGSearcher(
            base_url=settings.SEARXNG_BASE_URL,
            timeout=settings.SEARXNG_TIMEOUT,
            language=settings.SEARXNG_LANGUAGE or None,
            safesearch=settings.SEARXNG_SAFESEARCH,
        ),
        duckduckgo_searcher=DuckDuckGoBufferSearcher(
            timeout=settings.DUCKDUCKGO_TIMEOUT,
            region=settings.DUCKDUCKGO_REGION,
            safesearch=settings.DUCKDUCKGO_SAFESEARCH,
        ),
        tavily_searcher=TavilySearcher(
            api_key=settings.TAVILY_API_KEY,
            timeout=settings.TAVILY_TIMEOUT,
        ),
    )