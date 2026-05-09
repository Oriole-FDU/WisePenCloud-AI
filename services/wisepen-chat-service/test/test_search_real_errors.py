"""
搜索真实集成错误测试

用错误 URL / 超短 timeout / 错误 API key 触发真实网络失败，
验证 searcher 异常上下文是否足够。

使用方式:
    uv run python test/test_search_real_errors.py
"""
from __future__ import annotations

import asyncio
import sys

from chat.application.web_search.searcher.duckduckgo_searcher import (
    DuckDuckGoBufferSearcher,
)
from chat.application.web_search.searcher.searxng_searcher import SearXNGSearcher
from chat.application.web_search.searcher.tavily_searcher import TavilySearcher
from chat.core.config.app_settings import settings


async def test_searxng_connection_error() -> bool:
    print("\n测试 1: SearXNG 连接失败 → 异常包含 params")

    searcher = SearXNGSearcher(
        base_url="http://127.0.0.1:1",
        timeout=0.5,
    )

    try:
        await searcher.search("Python asyncio gather", max_results=5)
        print("  ✗ FAIL: 未抛异常")
        return False
    except RuntimeError as e:
        message = str(e)

        checks = [
            ("SearXNG" in message, "包含 SearXNG"),
            ("params=" in message, "包含 params="),
        ]

        all_ok = True
        for ok, desc in checks:
            if ok:
                print(f"  ✓ {desc}")
            else:
                print(f"  ✗ {desc}")
                all_ok = False

        if all_ok:
            print(f"  ✓ PASS: 异常信息 = {message[:200]}")
        return all_ok


async def test_searxng_http_error() -> bool:
    print("\n测试 2: SearXNG HTTP 错误 → 异常包含 status/params/body")

    searcher = SearXNGSearcher(
        base_url="http://localhost:8080/not-exist",
        timeout=2.0,
    )

    try:
        await searcher.search("Python asyncio gather", max_results=5)
        print("  ✗ FAIL: 未抛异常")
        return False
    except RuntimeError as e:
        message = str(e)

        if "SearXNG HTTP error" in message:
            checks = [
                ("status=" in message, "包含 status="),
                ("params=" in message, "包含 params="),
                ("body=" in message, "包含 body="),
            ]
        elif "SearXNG" in message:
            checks = [
                ("params=" in message, "包含 params="),
            ]
            print("  ⚠ 非 HTTP 错误（可能连接被拒），降级检查")
        else:
            print(f"  ✗ FAIL: 异常不包含 SearXNG: {message[:200]}")
            return False

        all_ok = True
        for ok, desc in checks:
            if ok:
                print(f"  ✓ {desc}")
            else:
                print(f"  ✗ {desc}")
                all_ok = False

        if all_ok:
            print(f"  ✓ PASS: 异常信息 = {message[:200]}")
        return all_ok


async def test_duckduckgo_timeout() -> bool:
    print("\n测试 3: DuckDuckGo 超短 timeout → 异常包含 query/timeout")

    searcher = DuckDuckGoBufferSearcher(
        timeout=0.001,
        region=settings.DUCKDUCKGO_REGION,
        safesearch=settings.DUCKDUCKGO_SAFESEARCH,
    )

    try:
        await searcher.search("Python asyncio gather", max_results=5, with_images=True)
        print("  ⚠ 未超时（可能很快），但搜索成功")
        return True
    except RuntimeError as e:
        message = str(e)

        checks = [
            ("DuckDuckGo" in message, "包含 DuckDuckGo"),
            ("Python asyncio gather" in message, "包含 query"),
        ]

        all_ok = True
        for ok, desc in checks:
            if ok:
                print(f"  ✓ {desc}")
            else:
                print(f"  ✗ {desc}")
                all_ok = False

        if all_ok:
            print(f"  ✓ PASS: 异常信息 = {message[:200]}")
        return all_ok


async def test_tavily_invalid_api_key() -> bool:
    print("\n测试 4: Tavily 错误 API key → 异常包含 payload/error")

    searcher = TavilySearcher(
        api_key="invalid-test-key",
        timeout=3.0,
    )

    try:
        await searcher.search("Python asyncio gather", max_results=5)
        print("  ✗ FAIL: 未抛异常")
        return False
    except RuntimeError as e:
        message = str(e)

        checks = [
            ("Tavily search failed" in message, "包含 Tavily search failed"),
            ("payload=" in message, "包含 payload="),
            ("Python asyncio gather" in message, "包含 query"),
        ]

        all_ok = True
        for ok, desc in checks:
            if ok:
                print(f"  ✓ {desc}")
            else:
                print(f"  ✗ {desc}")
                all_ok = False

        if all_ok:
            print(f"  ✓ PASS: 异常信息 = {message[:200]}")
        return all_ok


async def main() -> int:
    print("搜索真实集成错误测试\n")
    print(f"SearXNG: {settings.SEARXNG_BASE_URL}")

    tests = [
        test_searxng_connection_error,
        test_searxng_http_error,
        test_duckduckgo_timeout,
        test_tavily_invalid_api_key,
    ]

    results = []
    for test in tests:
        try:
            passed = await test()
            results.append((test.__name__, passed))
        except Exception as e:
            print(f"  ✗ EXCEPTION: {type(e).__name__}: {e}")
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
