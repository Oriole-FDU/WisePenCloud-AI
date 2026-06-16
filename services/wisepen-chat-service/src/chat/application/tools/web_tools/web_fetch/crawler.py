from __future__ import annotations

import asyncio
from collections import deque
from urllib.parse import urljoin, urlparse

from lxml import html as lxml_html

from common.logger import info, warn
from .cleaners.base import BaseCleaner
from .errors import WebFetchError
from .fetchers.base import BaseFetcher, RawFetchOutput
from .models import WebFetchResult
from .utils import judge_quality


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
        "_min_text_length",
        "_concurrency",
    )

    def __init__(
        self,
        *,
        httpx_fetcher: BaseFetcher,
        scrapling_fetcher: BaseFetcher,
        cleaner: BaseCleaner,
        min_text_length: int = 200,
        concurrency: int = 5,
    ) -> None:
        self._httpx_fetcher = httpx_fetcher
        self._scrapling_fetcher = scrapling_fetcher
        self._cleaner = cleaner
        self._min_text_length = min_text_length
        self._concurrency = concurrency

    async def crawl(
        self,
        seed_url: str,
        *,
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
                self._fetch_one_for_crawl(url, semaphore=semaphore)
                for url, _ in batch
            ]
            raws = await asyncio.gather(*tasks, return_exceptions=True)

            for (url, depth), raw in zip(batch, raws, strict=True):
                if isinstance(raw, Exception):
                    warn("web_crawl fetch failed", url=url, reason=str(raw))
                    continue
                if raw is None:
                    continue

                # 非 HTML 文件跳过（不递归，不 handoff）
                if raw.raw_html is None:
                    info("web_crawl skip non-html", url=url, label=raw.file_label)
                    continue

                # 清洗 + 构造结果
                result = self._build_result(raw)
                results.append(result)

                # 达到深度或页面数上限，不再扩展
                if depth >= max_depth or len(results) >= max_pages:
                    continue

                # 提取链接入队
                child_urls = _extract_links(raw.raw_html, url, base_domain, same_domain)
                for child_url in child_urls:
                    if child_url not in visited:
                        queue.append((child_url, depth + 1))

        return results

    async def _fetch_one_for_crawl(
        self,
        url: str,
        *,
        semaphore: asyncio.Semaphore,
    ) -> RawFetchOutput | None:
        """单个 URL 抓取，httpx → scrapling fallback。"""
        async with semaphore:
            try:
                raw = await self._httpx_fetcher.fetch(url)
            except WebFetchError as exc:
                warn("web_crawl httpx failed, fallback to scrapling", url=url, reason=exc.reason)
                try:
                    raw = await self._scrapling_fetcher.fetch(url)
                except WebFetchError as exc2:
                    warn("web_crawl scrapling failed", url=url, reason=exc2.reason)
                    return None

            # 质量判断：httpx 结果若质量不足，尝试 scrapling
            if raw.raw_html is not None:
                cleaned = self._cleaner.clean(raw.raw_html, url=raw.final_url or url)
                quality = judge_quality(raw=raw, cleaned=cleaned, min_text_length=self._min_text_length)
                if quality.should_fallback:
                    warn("web_crawl httpx quality insufficient, fallback to scrapling", url=url, reason=quality.reason)
                    try:
                        raw = await self._scrapling_fetcher.fetch(url)
                    except WebFetchError as exc2:
                        warn("web_crawl scrapling failed, using httpx result", url=url, reason=exc2.reason)

            return raw

    def _build_result(self, raw: RawFetchOutput) -> WebFetchResult:
        """将 RawFetchOutput 清洗为 WebFetchResult。"""
        warnings: list[str] = []
        cleaned = self._cleaner.clean(raw.raw_html or "", url=raw.final_url or raw.source_url)
        quality = judge_quality(raw=raw, cleaned=cleaned, min_text_length=self._min_text_length)
        if quality.should_fallback:
            warnings.append(f"content quality insufficient: {quality.reason}")

        return WebFetchResult(
            source_url=raw.source_url,
            final_url=raw.final_url,
            status_code=raw.status_code,
            content_type=raw.content_type,
            title=cleaned.title,
            markdown=cleaned.markdown,
            warnings=tuple(warnings),
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
