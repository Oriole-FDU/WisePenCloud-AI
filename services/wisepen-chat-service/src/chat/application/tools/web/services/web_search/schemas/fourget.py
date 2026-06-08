from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set
from urllib.parse import urlparse

from bs4 import BeautifulSoup
from bs4.element import Tag

from chat.application.tools.web.services.web_search.enums import SearcherName
from chat.application.tools.web.services.web_search.models import SearchResponse, SearchResult
from chat.application.tools.web.services.web_search.utils.results import (
    deduplicate_results_by_domain,
    is_valid_result,
)


@dataclass(frozen=True, slots=True)
class FourGetSearchRequest:
    query: str
    scraper: Optional[str] = None

    def to_params(self) -> Dict[str, str]:
        params = {"s": self.query}
        if self.scraper:
            params["scraper"] = self.scraper
        return params


_SKIP_DOMAINS: Set[str] = {
    "web.archive.org",
    "archive.ph",
    "ghostarchive.org",
    "arquivo.pt",
    "www.bing.com",
    "megalodon.jp",
    "duckduckgo.com",
}

_SKIP_TITLES: Set[str] = {
    "Archive.org",
    "Archive.is",
    "Ghostarchive",
    "Arquivo.pt",
    "Bing cache",
    "Megalodon",
    "Website",
    "Twitter",
}


def _is_inside_fourget_url_bar(link: Tag) -> bool:
    return link.find_parent("div", class_="url") is not None


def _should_skip_fourget_link(url: str, title: str) -> bool:
    parsed = urlparse(url)
    domain = parsed.netloc.lower()

    if parsed.scheme not in {"http", "https"}:
        return True

    if domain in _SKIP_DOMAINS:
        return True

    if title in _SKIP_TITLES:
        return True

    if url.lower().endswith((".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".ico")):
        return True

    return False


def _extract_fourget_snippet(block: Tag, title: str) -> str:
    cloned = BeautifulSoup(str(block), "html.parser")

    for noise in cloned.select("div.url"):
        noise.decompose()

    text = cloned.get_text(" ", strip=True)
    if text.startswith(title):
        text = text[len(title):].strip()

    return text


def map_fourget_html(
        html: str,
        *,
        query: str,
        scraper: Optional[str],
        max_results: int,
) -> SearchResponse:
    """
    阴的没边了，返回的是html，不是json
    """
    soup = BeautifulSoup(html, "html.parser")

    results: List[SearchResult] = []
    seen_urls: Set[str] = set()

    metadata: Dict[str, Any] = {}
    if scraper:
        metadata["scraper"] = scraper

    for block in soup.select("div.text-result"):
        title = ""
        url = ""

        for link in block.select("a[href]"):
            if not isinstance(link, Tag):
                continue

            if _is_inside_fourget_url_bar(link):
                continue

            candidate_url = str(link.get("href") or "").strip()
            candidate_title = link.get_text(" ", strip=True)

            if not candidate_url or not candidate_title:
                continue

            if _should_skip_fourget_link(
                    url=candidate_url,
                    title=candidate_title,
            ):
                continue

            title = candidate_title
            url = candidate_url
            break

        if not title or not url:
            continue

        if url in seen_urls:
            continue
        seen_urls.add(url)

        result = SearchResult(
            title=title,
            url=url,
            snippet=_extract_fourget_snippet(block=block, title=title),
            metadata=dict(metadata),
        )

        if is_valid_result(result):
            results.append(result)

    results = deduplicate_results_by_domain(results, max_per_domain=2)

    return SearchResponse(
        query=query,
        results=results[:max_results],
        source=SearcherName.FOURGET,
    )