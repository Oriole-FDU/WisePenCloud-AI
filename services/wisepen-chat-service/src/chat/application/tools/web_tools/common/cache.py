from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from hishel._core._headers import Headers, parse_cache_control
from hishel._core._spec import get_freshness_lifetime
from hishel._core.models import Response

from common.logger import info, warn

from .models import WebContentCacheValue
from .repository import WebContentCacheRepository


_DEFAULT_TTL = timedelta(hours=2)
_MAX_TTL = timedelta(days=1)


@dataclass(frozen=True, slots=True)
class _CachePolicy:
    expire_at: datetime
    no_store: bool = False


class WebContentCache:
    """Web 工具共享的 URL 内容缓存；缓存故障时由调用方继续实时处理。"""

    __slots__ = ("_repository",)

    def __init__(
        self,
        *,
        repository: WebContentCacheRepository | None,
    ) -> None:
        self._repository = repository

    async def read(
        self,
        *,
        url: str,
        cache_variant: str = "",
    ) -> WebContentCacheValue | None:
        if self._repository is None:
            return None

        try:
            now = datetime.now(timezone.utc)
            value = await self._repository.get_value(
                url=url,
                cache_variant=cache_variant,
            )
            if value is not None and not _is_expired(value, now=now):
                info("URL 内容缓存命中", url=url, cache_variant=cache_variant)
                return value
        except Exception as exc:
            warn(
                "URL 内容缓存读取失败，降级为实时处理",
                url=url,
                e=exc,
            )

        return None

    async def write(
        self,
        *,
        url: str,
        headers: dict[str, str],
        text: str,
        is_md: bool,
        raw_html: str | None = None,
        cache_variant: str = "",
    ) -> None:
        if self._repository is None or not text:
            return

        try:
            now = datetime.now(timezone.utc)
            policy = _compute_ttl(
                headers=headers,
                now=now,
            )
            if policy.no_store:
                info("URL 内容缓存被 no-store 指令跳过", url=url)
                return

            await self._repository.set_value(
                WebContentCacheValue(
                    canonical_url=url,
                    text=text,
                    is_md=is_md,
                    raw_html=raw_html,
                    expire_at=policy.expire_at,
                    cache_variant=cache_variant,
                )
            )
            info(
                "URL 内容缓存已写入",
                url=url,
                cache_variant=cache_variant,
            )
        except Exception as exc:
            warn("URL 内容缓存写入失败", url=url, e=exc)


def _compute_ttl(
    *,
    headers: dict[str, str],
    now: datetime,
) -> _CachePolicy:
    """依据 HTTP 缓存头计算过期时间，并限制最长缓存一天。"""
    cache_control = parse_cache_control(headers.get("cache-control"))
    if cache_control.no_store:
        return _CachePolicy(expire_at=now, no_store=True)

    response = Response(status_code=200, headers=Headers(headers))
    freshness_seconds = get_freshness_lifetime(response, True)
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

    return _CachePolicy(expire_at=now + min(ttl, _MAX_TTL))


def _is_expired(
    value: WebContentCacheValue,
    *,
    now: datetime,
) -> bool:
    expire_at = value.expire_at
    if expire_at.tzinfo is None:
        expire_at = expire_at.replace(tzinfo=timezone.utc)
    return now >= expire_at
