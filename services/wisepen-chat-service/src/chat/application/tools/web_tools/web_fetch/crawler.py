from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

from lxml import html as lxml_html

from chat.application.utils.url_security import (
    UrlSecurityError,
    validate_public_http_url_async,
)
from chat.application.tools.web_tools.common import (
    WebContentCache,
    WebContentCacheRepository,
)
from common.logger import warn

from .core.errors import UrlFetchError, UrlFetchUnsupportedUrlError
from .core.models import WebFetchResult
from .fetchers import WebFetcher
from .page_content import clean_html, should_fallback


@dataclass(frozen=True, slots=True)
class _CrawlPage:
    result: WebFetchResult
    raw_html: str | None


class WebCrawler:
    """复用 static -> stealthy 抓取链路，按 BFS 递归爬取 HTML。"""

    __slots__ = (
        "_cache",
        "_concurrency",
        "_min_text_length",
        "_static_fetcher",
        "_stealthy_fetcher",
    )

    def __init__(
        self,
        *,
        static_fetcher: WebFetcher,
        stealthy_fetcher: WebFetcher,
        content_cache_repository: WebContentCacheRepository | None = None,
        min_text_length: int = 200,
        concurrency: int = 16,
    ) -> None:
        self._static_fetcher = static_fetcher
        self._stealthy_fetcher = stealthy_fetcher
        self._cache = WebContentCache(
            repository=content_cache_repository,
        )
        self._min_text_length = min_text_length
        self._concurrency = concurrency

    async def crawl(
        self,
        seed_url: str,
        *,
        max_pages: int = 20,
        max_depth: int = 2,
        same_domain: bool = True,
    ) -> tuple[WebFetchResult, ...]:
        seed_url = await validate_public_http_url_async(seed_url.strip())
        base_domain = urlparse(seed_url).netloc
        queue: deque[tuple[str, int]] = deque([(seed_url, 0)])
        discovered = {seed_url}
        results: list[WebFetchResult] = []

        while queue and len(results) < max_pages:
            batch = [queue.popleft() for _ in range(min(len(queue), self._concurrency, max_pages - len(results)))]
            pages = await asyncio.gather(
                *(self._fetch_one(url) for url, _ in batch),
                return_exceptions=True,
            )

            for (url, depth), page in zip(batch, pages, strict=True):
                if isinstance(page, BaseException):
                    warn("web crawl page failed.", url=url, reason=str(page))
                    continue
                if page is None:
                    continue

                results.append(page.result)
                if (
                    depth >= max_depth
                    or len(results) >= max_pages
                    or page.raw_html is None
                ):
                    continue

                for child_url in _extract_links(
                    page.raw_html,
                    base_url=url,
                    base_domain=base_domain,
                    same_domain=same_domain,
                ):
                    if child_url in discovered:
                        continue
                    discovered.add(child_url)
                    queue.append((child_url, depth + 1))

        return tuple(results)

    async def _fetch_one(
        self,
        url: str,
    ) -> _CrawlPage | None:
        try:
            url = await validate_public_http_url_async(url.strip())
        except UrlSecurityError:
            return None

        cached = await self._cache.read(url=url)
        if cached is not None:
            if not cached.is_md:
                return None
            return _CrawlPage(
                result=WebFetchResult(
                    source_url=url,
                    text=cached.text,
                    is_md=True,
                ),
                raw_html=cached.raw_html,
            )

        used_stealthy = False
        try:
            raw = await self._static_fetcher.fetch(url)
        except UrlFetchUnsupportedUrlError:
            return None
        except UrlFetchError as exc:
            warn(
                "web crawl static failed, fallback to stealthy",
                url=url,
                reason=exc.reason,
            )
            used_stealthy = True
            try:
                raw = await self._stealthy_fetcher.fetch(url)
            except UrlFetchError:
                return None

        if raw.raw_html is None:
            return None

        # 页面批量并发抓取时，HTML 清洗必须在线程池中执行，避免阻塞 BFS 调度。
        markdown = await asyncio.to_thread(
            clean_html,
            raw.raw_html,
            url=raw.source_url,
        )
        needs_fallback = should_fallback(
            raw=raw,
            markdown=markdown,
            min_text_length=self._min_text_length,
        )
        if not used_stealthy and needs_fallback:
            try:
                fallback = await self._stealthy_fetcher.fetch(url)
            except UrlFetchError:
                pass
            else:
                if fallback.raw_html is not None:
                    raw = fallback
                    markdown = await asyncio.to_thread(
                        clean_html,
                        raw.raw_html,
                        url=raw.source_url,
                    )
                    needs_fallback = should_fallback(
                        raw=raw,
                        markdown=markdown,
                        min_text_length=self._min_text_length,
                    )

        result = WebFetchResult(
            source_url=raw.source_url,
            text=markdown or "",
            is_md=True,
        )
        if not needs_fallback:
            await self._cache.write(
                url=url,
                headers=raw.headers,
                text=result.text,
                is_md=result.is_md,
                raw_html=raw.raw_html,
            )
        return _CrawlPage(result=result, raw_html=raw.raw_html)


def _extract_links(
    raw_html: str,
    *,
    base_url: str,
    base_domain: str,
    same_domain: bool,
) -> list[str]:
    try:
        hrefs = lxml_html.fromstring(raw_html).xpath("//a/@href")
    except Exception:
        return []

    links: list[str] = []
    seen: set[str] = set()
    for href in hrefs:
        href = str(href).split("#", 1)[0].strip()
        if not href:
            continue
        try:
            absolute = urljoin(base_url, href)
            parsed = urlparse(absolute)
        except ValueError:
            continue
        if parsed.scheme not in {"http", "https"}:
            continue
        if same_domain and parsed.netloc != base_domain:
            continue
        if absolute in seen:
            continue

        seen.add(absolute)
        links.append(absolute)

    return links
