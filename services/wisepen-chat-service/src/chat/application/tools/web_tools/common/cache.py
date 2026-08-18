from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

from werkzeug.datastructures import ResponseCacheControl
from werkzeug.http import parse_cache_control_header

from chat.domain.repositories.web_content_cache_repo import WebContentCacheRepository
from common.logger import info, warn


@dataclass(frozen=True, slots=True)
class WebContentCacheValue:
    """URL 级缓存的完整正文；raw_html 仅供 crawl 继续发现链接。"""

    canonical_url: str
    text: str
    expire_at: datetime
    raw_html: str | None = None


_DEFAULT_TTL = timedelta(hours=2)
_MAX_TTL = timedelta(days=1)
_HEURISTIC_MAX_TTL = timedelta(days=7)


@dataclass(frozen=True, slots=True)
class _CachePolicy:
    expire_at: datetime
    no_store: bool = False


class WebContentCache:
    """共享 URL 内容缓存；Redis 故障时降级为实时抓取。"""

    __slots__ = ("_repository",)

    def __init__(self, *, repository: WebContentCacheRepository | None) -> None:
        self._repository = repository

    async def read(self, *, url: str) -> WebContentCacheValue | None:
        if self._repository is None:
            return None
        try:
            value = await self._repository.get_value(url=url)
            if value is not None and not _is_expired(value, now=datetime.now(timezone.utc)):
                info("URL 内容缓存命中", url=url)
                return value
        except Exception as exc:  # noqa: BLE001 - cache 故障必须降级为实时抓取
            warn("URL 内容缓存读取失败，降级为实时处理", url=url, e=exc)
        return None

    async def write(
        self,
        *,
        url: str,
        headers: dict[str, str],
        text: str,
        raw_html: str | None = None,
    ) -> None:
        if self._repository is None or not text:
            return
        try:
            now = datetime.now(timezone.utc)
            policy = _compute_ttl(headers=headers, now=now)
            if policy.no_store:
                info("URL 内容缓存被 no-store 指令跳过", url=url)
                return
            await self._repository.set_value(
                WebContentCacheValue(
                    canonical_url=url,
                    text=text,
                    raw_html=raw_html,
                    expire_at=policy.expire_at,
                )
            )
            info("URL 内容缓存已写入", url=url)
        except Exception as exc:  # noqa: BLE001 - cache 写入是附加能力
            warn("URL 内容缓存写入失败", url=url, e=exc)


def _compute_ttl(*, headers: dict[str, str], now: datetime) -> _CachePolicy:
    cc = parse_cache_control_header(
        _get_header(headers, "cache-control"), cls=ResponseCacheControl
    )
    if cc.no_store:
        return _CachePolicy(expire_at=now, no_store=True)

    freshness_seconds = _get_freshness_lifetime(headers, cc, now=now)
    ttl = (
        timedelta(seconds=freshness_seconds)
        if freshness_seconds is not None and freshness_seconds >= 0
        else _DEFAULT_TTL
    )
    if cc.must_revalidate or cc.no_cache or cc.max_age == 0:
        ttl = timedelta(0)
    return _CachePolicy(expire_at=now + min(ttl, _MAX_TTL))


def _get_freshness_lifetime(
    headers: dict[str, str],
    cc: ResponseCacheControl,
    *,
    now: datetime,
) -> int | None:
    if cc.s_maxage is not None:
        return cc.s_maxage
    if cc.max_age is not None:
        return cc.max_age

    expires_at = _parse_http_datetime(_get_header(headers, "expires"))
    if expires_at is not None:
        date_at = _parse_http_datetime(_get_header(headers, "date")) or now
        return int((expires_at - date_at).total_seconds())

    last_modified_at = _parse_http_datetime(_get_header(headers, "last-modified"))
    if last_modified_at is None:
        return None
    age = max(now - last_modified_at, timedelta(0))
    return int(min(age * 0.1, _HEURISTIC_MAX_TTL).total_seconds())


def _get_header(headers: dict[str, str], name: str) -> str | None:
    normalized_name = name.lower()
    for key, value in headers.items():
        if key.lower() == normalized_name:
            return value
    return None


def _parse_http_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _is_expired(value: WebContentCacheValue, *, now: datetime) -> bool:
    expire_at = value.expire_at
    if expire_at.tzinfo is None:
        expire_at = expire_at.replace(tzinfo=timezone.utc)
    return now >= expire_at