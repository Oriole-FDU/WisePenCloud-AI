from __future__ import annotations

from collections.abc import Iterable

from ..models import SearchResult


def dedupe_results(
    results: Iterable[SearchResult],
    *,
    limit: int,
) -> tuple[SearchResult, ...]:
    seen: set[str | None] = set()
    deduped: list[SearchResult] = []

    for result in results:
        if result.url in seen:
            continue
        seen.add(result.url)
        deduped.append(result)
        if len(deduped) >= limit:
            break

    return tuple(deduped)
