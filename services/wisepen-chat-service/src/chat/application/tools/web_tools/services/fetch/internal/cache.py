from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hishel._core._headers import Headers, parse_cache_control
from hishel._core._spec import get_freshness_lifetime
from hishel._core.models import Response

from common.logger import info, warn

from ..core.cache import WebContentCacheRepository
from ..core.models import (
    RawFetchOutput,
    WebContentCacheMode,
    WebContentCacheValue,
    WebFetchResult,
)


WEB_PUBLIC_SOURCE_SCOPE = "web_public"
WEB_CUSTOM_SOURCE_SCOPE = "web_custom"

_DEFAULT_TTL = timedelta(hours=2)
_MAX_TTL = timedelta(days=1)


@dataclass(frozen=True, slots=True)
class CachedFetchPage:
    result: WebFetchResult
    raw_html: str | None


@dataclass(frozen=True, slots=True)
class _CachePolicy:
    expire_at: datetime
    no_store: bool = False


class WebFetchCache:
    """网页抓取缓存；缓存故障降级为实时抓取。"""

    __slots__ = ("_repository",)

    def __init__(
        self,
        *,
        repository: WebContentCacheRepository | None,
    ) -> None:
        self._repository = repository

    async def read_page(
        self,
        *,
        url: str,
        user_id: str,
    ) -> CachedFetchPage | None:
        if self._repository is None:
            return None

        try:
            now = datetime.now(timezone.utc)

            # 当前用户的私有缓存优先；未命中时再读取所有用户共享的公共缓存。
            # 公共缓存不会反向覆盖或暴露其他用户的私有缓存。
            for mode in (
                WebContentCacheMode.PRIVATE,
                WebContentCacheMode.PUBLIC,
            ):
                value = await self._repository.get_value(
                    user_id=user_id,
                    url=url,
                    cache_mode=mode,
                )

                if value is None or _is_expired(value, now=now):
                    continue

                info(
                    "URL 内容缓存命中",
                    url=url,
                    cache_mode=mode.value,
                )

                return CachedFetchPage(
                    result=WebFetchResult(
                        source_url=url,
                        text=value.text,
                        is_md=value.is_md,
                    ),
                    raw_html=value.raw_html,
                )

        except Exception as exc:
            warn(
                "URL 内容缓存读取失败，降级为实时抓取",
                url=url,
                e=exc,
            )

        return None

    async def write_result(
        self,
        *,
        url: str,
        user_id: str,
        source_scope: str,
        raw: RawFetchOutput,
        result: WebFetchResult,
    ) -> None:
        if self._repository is None or not result.text:
            return

        try:
            now = datetime.now(timezone.utc)

            mode = _cache_mode(source_scope)
            ttl = _compute_ttl(
                headers=raw.headers,
                now=now,
                is_shared_cache=mode is WebContentCacheMode.PUBLIC,
            )

            if ttl.no_store:
                info(
                    "URL 内容缓存被 no-store 指令跳过",
                    url=url,
                )
                return

            await self._repository.set_value(
                WebContentCacheValue(
                    user_id=user_id,
                    canonical_url=url,
                    cache_mode=mode,
                    text=result.text,
                    is_md=result.is_md,
                    raw_html=raw.raw_html,
                    expire_at=ttl.expire_at,
                )
            )

            info(
                "URL 内容缓存已写入",
                url=url,
                cache_mode=mode.value,
            )

        except Exception as exc:
            warn(
                "URL 内容缓存写入失败",
                url=url,
                e=exc,
            )


def _compute_ttl(
    *,
    headers: dict[str, str],
    now: datetime,
    is_shared_cache: bool = False,
) -> _CachePolicy:
    """依据 HTTP 缓存头计算过期时间，并限制最长缓存一天。"""
    cache_control = parse_cache_control(
        headers.get("cache-control")
    )

    if cache_control.no_store:
        return _CachePolicy(
            expire_at=now,
            no_store=True,
        )

    response = Response(
        status_code=200,
        headers=Headers(headers),
    )

    freshness_seconds = get_freshness_lifetime(
        response,
        is_shared_cache,
    )

    ttl = (
        timedelta(seconds=freshness_seconds)
        if freshness_seconds is not None and freshness_seconds >= 0
        else _DEFAULT_TTL
    )

    if (
        cache_control.must_revalidate
        or cache_control.no_cache is True
        or cache_control.max_age == 0
    ):
        ttl = timedelta(0)

    return _CachePolicy(
        expire_at=now + min(ttl, _MAX_TTL)
    )


def _cache_mode(source_scope: str) -> WebContentCacheMode:
    if source_scope == WEB_PUBLIC_SOURCE_SCOPE:
        return WebContentCacheMode.PUBLIC

    if source_scope == WEB_CUSTOM_SOURCE_SCOPE:
        return WebContentCacheMode.PRIVATE

    raise ValueError(
        f"Unsupported web content source scope: {source_scope!r}"
    )


def _is_expired(
    value: WebContentCacheValue,
    *,
    now: datetime,
) -> bool:
    expire_at = value.expire_at

    if expire_at.tzinfo is None:
        expire_at = expire_at.replace(
            tzinfo=timezone.utc,
        )

    return now >= expire_at
