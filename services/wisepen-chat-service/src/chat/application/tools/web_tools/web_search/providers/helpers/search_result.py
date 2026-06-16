from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any
from urllib.parse import urlparse


def is_http_url(url: str) -> bool:
    """判断字符串是否为合法的 http/https URL。"""
    parsed = urlparse(url)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def is_valid_result(*, title: str, url: str) -> bool:
    """搜索结果最低可用性校验：title 非空且 url 合法。"""
    return bool(title) and is_http_url(url)


def dedupe_by_url(
    items: Iterable[Any],
    *,
    url_getter: Callable[[Any], str],
    limit: int,
) -> tuple[Any, ...]:
    """按 URL 去重并截断，保留首次出现的条目。"""
    seen: set[str] = set()
    results: list[Any] = []
    for item in items:
        url = url_getter(item)
        if url in seen:
            continue
        seen.add(url)
        results.append(item)
        if len(results) >= limit:
            break
    return tuple(results)
