from __future__ import annotations

import asyncio
from typing import Optional, Tuple

from cachetools import TTLCache

from chat.application.web_search.models import SearchResponse

__all__ = [
    "SearchCache",
    "SearchCacheKey",
    "make_search_cache_key",
]

SearchCacheKey = Tuple[str, int, bool]


class SearchCache:
    def __init__(
        self,
        *,
        fresh_ttl: int = 3600,
        stale_ttl: int = 86400,
        maxsize: int = 1024,
    ) -> None:
        self._fresh_cache: TTLCache[SearchCacheKey, SearchResponse] = TTLCache(
            maxsize=maxsize,
            ttl=fresh_ttl,
        )
        self._stale_cache: TTLCache[SearchCacheKey, SearchResponse] = TTLCache(
            maxsize=maxsize,
            ttl=stale_ttl,
        )
        self._lock = asyncio.Lock()

    async def get_fresh(
        self,
        key: SearchCacheKey,
    ) -> Optional[SearchResponse]:
        async with self._lock:
            return self._fresh_cache.get(key)

    async def get_stale(
        self,
        key: SearchCacheKey,
    ) -> Optional[SearchResponse]:
        async with self._lock:
            return self._stale_cache.get(key)

    async def set(
        self,
        key: SearchCacheKey,
        response: SearchResponse,
    ) -> None:
        async with self._lock:
            self._fresh_cache[key] = response
            self._stale_cache[key] = response


def make_search_cache_key(
    *,
    query: str,
    max_results: int,
    with_images: bool,
) -> SearchCacheKey:
    normalized_query = " ".join(query.strip().lower().split())
    return normalized_query, max_results, with_images
