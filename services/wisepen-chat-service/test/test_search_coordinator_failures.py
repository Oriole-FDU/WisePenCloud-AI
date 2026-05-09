"""
搜索降级链确定性单元测试

用 fake searcher 主动抛异常 / 返回空结果，验证降级链路和日志。
不依赖网络，不消耗 Tavily 额度。

使用方式:
    uv run python test/test_search_coordinator_failures.py
"""
from __future__ import annotations

import asyncio
import sys
from typing import Optional

from chat.application.web_search.cache import SearchCache
from chat.application.web_search.models import SearchResponse, SearchResult
from chat.application.web_search.search_coordinator import SearchCoordinator


class RaisingSearcher:
    def __init__(self, message: str) -> None:
        self._message = message

    async def search(
        self,
        query: str,
        *,
        max_results: int = 5,
        with_images: bool = False,
    ) -> SearchResponse:
        raise RuntimeError(self._message)


class EmptySearcher:
    async def search(
        self,
        query: str,
        *,
        max_results: int = 5,
        with_images: bool = False,
    ) -> SearchResponse:
        return SearchResponse(query=query)


class SuccessSearcher:
    async def search(
        self,
        query: str,
        *,
        max_results: int = 5,
        with_images: bool = False,
    ) -> SearchResponse:
        return SearchResponse(
            query=query,
            results=(
                SearchResult(
                    title="Example result",
                    url="https://example.com",
                    snippet="This is an example search result.",
                ),
            ),
        )


def make_coordinator(
    *,
    searxng: object = None,
    duckduckgo: object = None,
    tavily: object = None,
    continue_on_empty: bool = True,
    fresh_ttl: int = 60,
    stale_ttl: int = 3600,
) -> SearchCoordinator:
    return SearchCoordinator(
        cache=SearchCache(fresh_ttl=fresh_ttl, stale_ttl=stale_ttl, maxsize=64),
        searxng_searcher=searxng or SuccessSearcher(),
        duckduckgo_searcher=duckduckgo or SuccessSearcher(),
        tavily_searcher=tavily or SuccessSearcher(),
        continue_on_empty=continue_on_empty,
    )


async def test_searxng_exception_then_duckduckgo_success() -> bool:
    print("\n测试 1: SearXNG 抛异常 → DuckDuckGo 成功")

    coordinator = make_coordinator(
        searxng=RaisingSearcher("injected searxng request failure"),
        duckduckgo=SuccessSearcher(),
        tavily=SuccessSearcher(),
    )

    response = await coordinator.search("Python asyncio gather", max_results=5)

    if response is None:
        print("  ✗ FAIL: 返回 None")
        return False

    if response.source != "duckduckgo":
        print(f"  ✗ FAIL: source={response.source}，期望 duckduckgo")
        return False

    print(f"  ✓ PASS: source={response.source}, results={len(response.results)}")
    return True


async def test_searxng_duckduckgo_exception_then_tavily_success() -> bool:
    print("\n测试 2: SearXNG + DuckDuckGo 抛异常 → Tavily 成功")

    coordinator = make_coordinator(
        searxng=RaisingSearcher("injected searxng failure"),
        duckduckgo=RaisingSearcher("injected duckduckgo failure"),
        tavily=SuccessSearcher(),
    )

    response = await coordinator.search("unique-test-query-error-001", max_results=5)

    if response is None:
        print("  ✗ FAIL: 返回 None")
        return False

    if response.source != "tavily":
        print(f"  ✗ FAIL: source={response.source}，期望 tavily")
        return False

    print(f"  ✓ PASS: source={response.source}, results={len(response.results)}")
    return True


async def test_empty_result_then_fallback_success() -> bool:
    print("\n测试 3: SearXNG 返回空 → DuckDuckGo 成功")

    coordinator = make_coordinator(
        searxng=EmptySearcher(),
        duckduckgo=SuccessSearcher(),
        tavily=SuccessSearcher(),
    )

    response = await coordinator.search("empty result fallback test", max_results=5)

    if response is None:
        print("  ✗ FAIL: 返回 None")
        return False

    if response.source != "duckduckgo":
        print(f"  ✗ FAIL: source={response.source}，期望 duckduckgo")
        return False

    print(f"  ✓ PASS: source={response.source}, results={len(response.results)}")
    return True


async def test_all_searchers_raise_returns_none() -> bool:
    print("\n测试 4: 全部抛异常 → 返回 None")

    coordinator = make_coordinator(
        searxng=RaisingSearcher("injected searxng failure"),
        duckduckgo=RaisingSearcher("injected duckduckgo failure"),
        tavily=RaisingSearcher("injected tavily failure"),
    )

    response = await coordinator.search("all engines fail test", max_results=5)

    if response is not None:
        print(f"  ✗ FAIL: 返回了结果 source={response.source}，期望 None")
        return False

    print("  ✓ PASS: 全部失败时返回 None")
    return True


async def test_errors_then_stale_cache_hit() -> bool:
    print("\n测试 5: SearXNG+DDG 抛异常 → Stale Cache 命中")

    cache = SearchCache(fresh_ttl=1, stale_ttl=3600, maxsize=64)
    query = "stale cache error fallback test"

    seed_coordinator = SearchCoordinator(
        cache=cache,
        searxng_searcher=SuccessSearcher(),
        duckduckgo_searcher=RaisingSearcher("should not be used"),
        tavily_searcher=RaisingSearcher("should not be used"),
        continue_on_empty=True,
    )

    first = await seed_coordinator.search(query, max_results=5)
    if first is None or first.source != "searxng":
        print(f"  ✗ FAIL: 种子查询失败 source={first.source if first else None}")
        return False

    await asyncio.sleep(1.1)

    coordinator = SearchCoordinator(
        cache=cache,
        searxng_searcher=RaisingSearcher("injected searxng failure before stale cache"),
        duckduckgo_searcher=RaisingSearcher("injected duckduckgo failure before stale cache"),
        tavily_searcher=RaisingSearcher("tavily should not be called if stale cache works"),
        continue_on_empty=True,
    )

    response = await coordinator.search(query, max_results=5)

    if response is None:
        print("  ✗ FAIL: 返回 None，期望 stale_cache")
        return False

    if response.source != "stale_cache":
        print(f"  ✗ FAIL: source={response.source}，期望 stale_cache")
        return False

    print(f"  ✓ PASS: source=stale_cache, results={len(response.results)}")
    return True


async def main() -> int:
    print("搜索降级链确定性单元测试\n")

    tests = [
        test_searxng_exception_then_duckduckgo_success,
        test_searxng_duckduckgo_exception_then_tavily_success,
        test_empty_result_then_fallback_success,
        test_all_searchers_raise_returns_none,
        test_errors_then_stale_cache_hit,
    ]

    results = []
    for test in tests:
        try:
            passed = await test()
            results.append((test.__name__, passed))
        except Exception as e:
            print(f"  ✗ EXCEPTION: {e}")
            results.append((test.__name__, False))

    print("\n" + "=" * 60)
    print("汇总")
    print("=" * 60)

    passed = sum(1 for _, ok in results if ok)
    total = len(results)

    for name, ok in results:
        print(f"  {'✓' if ok else '✗'} {name}")

    print(f"\n通过: {passed}/{total}")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
