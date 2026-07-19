from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any
from urllib.parse import urlparse


def as_str(value: object) -> str:
    return "" if value is None else str(value).strip()


def as_str_or_none(value: object) -> str | None:
    return as_str(value) or None


def as_str_tuple(value: object) -> tuple[str, ...]:
    values = (value,) if isinstance(value, str) else value
    if not isinstance(values, list | tuple):
        return ()
    return tuple(text for item in values if (text := as_str(item)))


def as_dict_tuple(value: object) -> tuple[dict[str, object], ...]:
    if not isinstance(value, list | tuple):
        return ()
    return tuple(item for item in value if isinstance(item, dict))


def has_search_result_fields(*, title: str, url: str) -> bool:
    parsed = urlparse(url)
    return bool(title) and parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def dedupe_by_url(
    items: Iterable[Any],
    *,
    url_getter: Callable[[Any], str],
    limit: int,
) -> tuple[Any, ...]:
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
