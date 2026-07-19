from __future__ import annotations

from collections.abc import Iterable

from .core.models import ProviderSearchResult


def dedupe_results(
    results: Iterable[ProviderSearchResult],
    *,
    limit: int,
) -> tuple[ProviderSearchResult, ...]:
    seen: set[str] = set()
    deduped: list[ProviderSearchResult] = []

    for result in results:
        if result.url in seen:
            continue
        seen.add(result.url)
        deduped.append(result)
        if len(deduped) >= limit:
            break

    return tuple(deduped)
