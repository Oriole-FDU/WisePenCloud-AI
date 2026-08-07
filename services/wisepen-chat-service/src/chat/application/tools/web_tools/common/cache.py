from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from chat.domain.repositories import WebContentCacheRepository

from common.logger import info, warn


@dataclass(frozen=True, slots=True)
class WebContentCacheValue:
    canonical_url: str
    text: str
    is_md: bool
    expire_at: datetime
    raw_html: str | None = None
    cache_variant: str = ""


_DEFAULT_TTL = timedelta(hours=2)
_MAX_TTL = timedelta(days=1)
_HEURISTIC_MAX_TTL = timedelta(days=7)


@dataclass(frozen=True, slots=True)
class _CachePolicy:
    expire_at: datetime
    no_store: bool = False


@dataclass(frozen=True, slots=True)
class _CacheControl:
    no_store: bool = False
    no_cache: bool = False
    must_revalidate: bool = False
    max_age: int | None = None
    s_maxage: int | None = None


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
            warn("URL 内容缓存读取失败，降级为实时处理", url=url, e=exc)

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
            info("URL 内容缓存已写入", url=url, cache_variant=cache_variant,)
        except Exception as exc:
            warn("URL 内容缓存写入失败", url=url, e=exc)


def _compute_ttl(
    *,
    headers: dict[str, str],
    now: datetime,
) -> _CachePolicy:
    """依据 HTTP 缓存头计算过期时间，并限制最长缓存一天。"""
    cache_control = _parse_cache_control(_get_header(headers, "cache-control"))
    if cache_control.no_store:
        return _CachePolicy(expire_at=now, no_store=True)

    freshness_seconds = _get_freshness_lifetime(headers, cache_control, now=now)
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


def _parse_cache_control(value: str | None) -> _CacheControl:
    values: dict[str, str | None] = {}
    for directive in _split_cache_control_directives(value):
        name, separator, raw_value = directive.partition("=")
        normalized_name = name.strip().lower()
        if not normalized_name:
            continue
        values[normalized_name] = raw_value.strip().strip('"') if separator else None

    return _CacheControl(
        no_store="no-store" in values,
        no_cache="no-cache" in values,
        must_revalidate="must-revalidate" in values,
        max_age=_parse_delta_seconds(values.get("max-age")),
        s_maxage=_parse_delta_seconds(values.get("s-maxage")),
    )


def _split_cache_control_directives(value: str | None) -> list[str]:
    if not value:
        return []

    directives: list[str] = []
    start = 0
    in_quote = False
    escaped = False
    for index, char in enumerate(value):
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = in_quote
            continue
        if char == '"':
            in_quote = not in_quote
            continue
        if char == "," and not in_quote:
            directives.append(value[start:index].strip())
            start = index + 1
    directives.append(value[start:].strip())
    return directives


def _parse_delta_seconds(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        seconds = int(value)
    except ValueError:
        return None
    return max(seconds, 0)


def _get_freshness_lifetime(
    headers: dict[str, str],
    cache_control: _CacheControl,
    *,
    now: datetime,
) -> int | None:
    if cache_control.s_maxage is not None:
        return cache_control.s_maxage
    if cache_control.max_age is not None:
        return cache_control.max_age

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


def _is_expired(
    value: WebContentCacheValue,
    *,
    now: datetime,
) -> bool:
    expire_at = value.expire_at
    if expire_at.tzinfo is None:
        expire_at = expire_at.replace(tzinfo=timezone.utc)
    return now >= expire_at
