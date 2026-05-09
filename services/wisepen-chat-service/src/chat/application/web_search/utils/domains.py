from __future__ import annotations

from typing import Dict, List, Tuple
from urllib.parse import urlparse

from chat.application.web_search.models.common import SearchResult

__all__ = [
    "extract_domain",
    "count_unique_domains",
    "deduplicate_results_by_domain",
]


def extract_domain(url: str) -> str:
    try:
        parsed = urlparse(url)
    except Exception:
        return ""

    domain = parsed.hostname
    if not domain:
        return ""

    return domain.lower().removeprefix("www.")


def count_unique_domains(results: Tuple[SearchResult, ...]) -> int:
    domains = {extract_domain(result.url) for result in results}
    domains.discard("")
    return len(domains)


def deduplicate_results_by_domain(
    results: Tuple[SearchResult, ...],
    *,
    max_per_domain: int = 2,
) -> Tuple[SearchResult, ...]:
    domain_counts: Dict[str, int] = {}
    deduped: List[SearchResult] = []

    for result in results:
        domain = extract_domain(result.url)

        if not domain:
            deduped.append(result)
            continue

        count = domain_counts.get(domain, 0)
        if count >= max_per_domain:
            continue

        domain_counts[domain] = count + 1
        deduped.append(result)

    return tuple(deduped)