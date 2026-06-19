from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

from lxml import html as lxml_html

from common.logger import info, warn
from chat.application.tools.web_tools.web_content_cache.refresh_queue import (
    WEB_FETCH_REFRESH_JOB,
    WebContentCacheRefreshTaskPublisher,
)
from chat.application.tools.web_tools.web_content_cache.repository import (
    WebContentCacheRepository,
)
from chat.application.tools.web_tools.web_content_cache.service import (
    HtmlCacheWrite,
    WebContentCacheService,
)
from .cleaners.base import BaseCleaner
from .errors import WebFetchError
from .fetchers.base import BaseFetcher, RawFetchOutput
from .models import WebFetchResult
from .utils import judge_quality

_REFRESH_LOCK_TTL_SECONDS = 300


@dataclass(frozen=True, slots=True)
class _CrawlPage:
    result: WebFetchResult
    raw_html: str | None


class WebCrawlService:
    """Web 递归爬取服务。

    复用 HttpxFetcher / ScraplingFetcher 的 fallback 链路抓取 HTML 页面，
    用 lxml 从 raw_html 提取同域链接，BFS 递归爬取。

    设计要点：
    - 直接用底层 fetcher（非 FetchCoordinator.fetch_one），因为 crawl 需要 raw_html 提取链接，
      而 fetch_one 返回的 WebFetchResult 只含清洗后的 markdown
    - 非 HTML 文件（PDF/图片等）自然跳过：fetcher 返回 file_path 而非 raw_html，
      crawler 不递归非 HTML，也不做 handoff（crawl 目标是 HTML 页面集合）
    - 复用 cleaner 保持清洗一致性，复用 judge_quality 让低质量页面也触发 scrapling fallback
    - BFS + visited 集合防环，max_pages/max_depth 限制规模
    """

    __slots__ = (
        "_httpx_fetcher",
        "_scrapling_fetcher",
        "_cleaner",
        "_content_cache_service",
        "_min_text_length",
        "_concurrency",
    )

    def __init__(
        self,
        *,
        httpx_fetcher: BaseFetcher,
        scrapling_fetcher: BaseFetcher,
        cleaner: BaseCleaner,
        content_cache_repository: WebContentCacheRepository | None = None,
        refresh_task_publisher: WebContentCacheRefreshTaskPublisher | None = None,
        min_text_length: int = 200,
        concurrency: int = 5,
    ) -> None:
        self._httpx_fetcher = httpx_fetcher
        self._scrapling_fetcher = scrapling_fetcher
        self._cleaner = cleaner
        self._content_cache_service = WebContentCacheService(
            repository=content_cache_repository,
            refresh_task_publisher=refresh_task_publisher,
        )
        self._min_text_length = min_text_length
        self._concurrency = concurrency

    async def crawl(
        self,
        seed_url: str,
        *,
        user_id: str,
        session_id: str,
        source_scope: str = "web_public",
        max_pages: int = 100,
        max_depth: int = 3,
        same_domain: bool = True,
    ) -> list[WebFetchResult]:
        """BFS 递归爬取 seed_url。"""
        base_domain = urlparse(seed_url).netloc
        queue: deque[tuple[str, int]] = deque([(seed_url, 0)])
        visited: set[str] = set()
        results: list[WebFetchResult] = []
        semaphore = asyncio.Semaphore(self._concurrency)

        while queue and len(results) < max_pages:
            # 按并发度取一批
            batch: list[tuple[str, int]] = []
            while queue and len(batch) < self._concurrency and len(results) + len(batch) < max_pages:
                url, depth = queue.popleft()
                if url in visited:
                    continue
                visited.add(url)
                batch.append((url, depth))

            if not batch:
                continue

            # 并发抓取本批
            tasks = [
                self._fetch_one_for_crawl(
                    url,
                    semaphore=semaphore,
                    user_id=user_id,
                    session_id=session_id,
                    source_scope=source_scope,
                )
                for url, _ in batch
            ]
            pages = await asyncio.gather(*tasks, return_exceptions=True)

            for (url, depth), page in zip(batch, pages, strict=True):
                if isinstance(page, Exception):
                    warn("web_crawl fetch failed", url=url, reason=str(page))
                    continue
                if page is None:
                    continue

                results.append(page.result)

                # 达到深度或页面数上限，不再扩展
                if depth >= max_depth or len(results) >= max_pages or page.raw_html is None:
                    continue

                # 提取链接入队
                child_urls = _extract_links(page.raw_html, url, base_domain, same_domain)
                for child_url in child_urls:
                    if child_url not in visited:
                        queue.append((child_url, depth + 1))

        return results

    async def _fetch_one_for_crawl(
        self,
        url: str,
        *,
        semaphore: asyncio.Semaphore,
        user_id: str,
        session_id: str,
        source_scope: str,
    ) -> _CrawlPage | None:
        """单个 URL 抓取，httpx → scrapling fallback。"""
        async with semaphore:
            cached = await self._read_cached_page(
                url=url,
                user_id=user_id,
                session_id=session_id,
                source_scope=source_scope,
            )
            if cached is not None:
                return cached

            try:
                raw = await self._httpx_fetcher.fetch(url)
            except WebFetchError as exc:
                warn("web_crawl httpx failed, fallback to scrapling", url=url, reason=exc.reason)
                try:
                    raw = await self._scrapling_fetcher.fetch(url)
                except WebFetchError as exc2:
                    warn("web_crawl scrapling failed", url=url, reason=exc2.reason)
                    return None

            if raw.raw_html is None:
                info("web_crawl skip non-html", url=url, label=raw.file_label)
                return None

            # 质量判断：httpx 结果若质量不足，尝试 scrapling
            cleaned = self._cleaner.clean(raw.raw_html, url=raw.final_url or url)
            quality = judge_quality(raw=raw, cleaned=cleaned, min_text_length=self._min_text_length)
            if quality.should_fallback:
                warn("web_crawl httpx quality insufficient, fallback to scrapling", url=url, reason=quality.reason)
                try:
                    fallback_raw = await self._scrapling_fetcher.fetch(url)
                    if fallback_raw.raw_html is not None:
                        raw = fallback_raw
                except WebFetchError as exc2:
                    warn("web_crawl scrapling failed, using httpx result", url=url, reason=exc2.reason)

            page = self._build_page(raw, source_scope=source_scope)
            if not page.result.warnings:
                await self._write_html_cache(
                    url=url,
                    user_id=user_id,
                    source_scope=source_scope,
                    raw=raw,
                    result=page.result,
                )
            return page

    def _build_page(self, raw: RawFetchOutput, *, source_scope: str) -> _CrawlPage:
        """将 RawFetchOutput 清洗为 WebFetchResult。"""
        warnings: list[str] = []
        cleaned = self._cleaner.clean(raw.raw_html or "", url=raw.final_url or raw.source_url)
        quality = judge_quality(raw=raw, cleaned=cleaned, min_text_length=self._min_text_length)
        if quality.should_fallback:
            warnings.append(f"content quality insufficient: {quality.reason}")

        return _CrawlPage(
            result=WebFetchResult(
                source_url=raw.source_url,
                final_url=raw.final_url,
                status_code=raw.status_code,
                content_type=raw.content_type,
                title=cleaned.title,
                markdown=cleaned.markdown,
                warnings=tuple(warnings),
                source_scope=source_scope,
            ),
            raw_html=raw.raw_html,
        )

    async def _read_cached_page(
        self,
        *,
        url: str,
        user_id: str,
        session_id: str,
        source_scope: str,
    ) -> _CrawlPage | None:
        cached = await self._content_cache_service.read_markdown_page(
            url=url,
            user_id=user_id,
            session_id=session_id,
            source_scope=source_scope,
            refresh_job_prefix="web_crawl",
            refresh_task_name=WEB_FETCH_REFRESH_JOB,
            refresh_lock_ttl_seconds=_REFRESH_LOCK_TTL_SECONDS,
        )
        if cached is None:
            return None

        return _CrawlPage(
            result=WebFetchResult(
                source_url=cached.source_url,
                final_url=cached.final_url,
                status_code=cached.status_code,
                content_type=cached.content_type,
                title=cached.title,
                markdown=cached.markdown,
                source_scope=source_scope,
            ),
            raw_html=cached.raw_html,
        )

    async def _write_html_cache(
        self,
        *,
        url: str,
        user_id: str,
        source_scope: str,
        raw: RawFetchOutput,
        result: WebFetchResult,
    ) -> None:
        await self._content_cache_service.write_html_markdown(
            HtmlCacheWrite(
                url=url,
                user_id=user_id,
                source_scope=source_scope,
                final_url=result.final_url,
                status_code=result.status_code,
                content_type=result.content_type,
                raw_html=raw.raw_html,
                markdown=result.markdown,
                title=result.title,
                headers=raw.headers,
                fetcher=raw.fetcher,
                cleaner=self._cleaner.name,
                producer="web_crawl",
            )
        )


def _extract_links(
    raw_html: str,
    base_url: str,
    base_domain: str,
    same_domain: bool,
) -> list[str]:
    """从 HTML 提取链接，去锚点、规范化、same_domain 过滤。返回去重结果。"""
    try:
        tree = lxml_html.fromstring(raw_html)
    except Exception:
        return []

    seen: set[str] = set()
    result: list[str] = []

    for href in tree.xpath("//a/@href"):
        if not href:
            continue
        # 去锚点
        href = href.split("#", 1)[0].strip()
        if not href:
            continue
        # 相对路径转绝对
        absolute = urljoin(base_url, href)
        if not absolute.startswith(("http://", "https://")):
            continue
        # 同域过滤
        if same_domain and urlparse(absolute).netloc != base_domain:
            continue
        if absolute not in seen:
            seen.add(absolute)
            result.append(absolute)

    return result
