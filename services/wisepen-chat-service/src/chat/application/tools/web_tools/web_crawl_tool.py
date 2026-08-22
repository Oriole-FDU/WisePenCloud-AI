from __future__ import annotations

import asyncio
from collections import deque
from contextlib import suppress
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin, urlparse

from common.logger import warn
from lxml import html as lxml_html

from chat.application.tools.core import (
    ToolDefinition,
    ToolExecutionError,
    ToolLLMSpec,
    ToolParametersSchema,
    ToolPolicy,
    ToolRiskLevel,
)
from chat.application.tools.core.output_cache import cacheable_tool_output

from .common import (
    UrlSecurityError,
    WebContentCache,
    validate_public_http_url_async,
)
from .fetchers import (
    RawFetchOutput,
    UrlFetchError,
    UrlFetchHttpError,
    UrlFetchUnsupportedUrlError,
    WebFetcher,
)
from .page_content import clean_html, should_fallback

DEFAULT_MAX_PAGES = 20
DEFAULT_MAX_DEPTH = 2
MAX_MAX_PAGES = 100
MAX_MAX_DEPTH = 5
DEFAULT_CONCURRENCY = 16
DEFAULT_MIN_TEXT_LENGTH = 200
_NOT_RETRYABLE_HTTP_REASONS = {"http 404", "http 410"}

_PARAMETERS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "seed_url": {
            "type": "string",
            "minLength": 1,
            "description": "Complete public HTTP(S) seed URL for the first HTML page.",
        },
        "max_pages": {
            "type": "integer",
            "minimum": 1,
            "maximum": MAX_MAX_PAGES,
            "default": DEFAULT_MAX_PAGES,
            "description": "Maximum number of successfully fetched HTML pages.",
        },
        "max_depth": {
            "type": "integer",
            "minimum": 0,
            "maximum": MAX_MAX_DEPTH,
            "default": DEFAULT_MAX_DEPTH,
            "description": "Maximum link distance from the seed page.",
        },
        "same_domain": {
            "type": "boolean",
            "default": True,
            "description": "Only follow links on the seed URL domain when true.",
        },
    },
    "required": ["seed_url"],
    "additionalProperties": False,
}


@dataclass(frozen=True, slots=True)
class _CrawlPage:
    source_url: str
    text: str
    headers: dict[str, str]
    raw_html: str | None


class WebCrawlTool:
    """直接拥有 BFS、链接发现、两阶段抓取和 URL cache 读取。"""

    def __init__(
        self,
        *,
        static_fetcher: WebFetcher,
        stealthy_fetcher: WebFetcher,
        cache: WebContentCache,
        concurrency: int = DEFAULT_CONCURRENCY,
        min_text_length: int = DEFAULT_MIN_TEXT_LENGTH,
    ) -> None:
        self._static_fetcher = static_fetcher
        self._stealthy_fetcher = stealthy_fetcher
        self._cache = cache
        self._concurrency = max(1, int(concurrency))
        self._min_text_length = max(1, int(min_text_length))
        self._definition = ToolDefinition(
            llm_spec=ToolLLMSpec(
                name="web_crawl",
                description=(
                    "Discover and fetch linked HTML pages breadth-first from one public seed URL. "
                    "Use this when the exact pages are not all known. The crawl is bounded by "
                    "max_pages, max_depth, and same_domain; direct document extraction is not supported."
                ),
                parameters_schema=ToolParametersSchema(_PARAMETERS_SCHEMA),
            ),
            policy=ToolPolicy(
                expose_by_default=True,
                persist_output=True,
                risk_level=ToolRiskLevel.MEDIUM,
                timeout_seconds=300.0,
                # 正文已经由 claim-check 装饰器转存，模型只接收 preview/receipt。
                max_output_chars=None,
            ),
        )

    @property
    def definition(self) -> ToolDefinition:
        return self._definition

    @cacheable_tool_output(paths=("pages.*.text",))
    async def execute(
        self,
        context: dict[str, Any],
        config: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        del context, config
        seed_url = kwargs["seed_url"].strip()
        try:
            seed_url = await validate_public_http_url_async(seed_url)
        except UrlSecurityError as exc:
            raise ToolExecutionError(
                reason="web_crawl_invalid_seed",
                detail_reason=str(exc),
                retryable=False,
            ) from exc

        max_pages = kwargs["max_pages"]
        max_depth = kwargs["max_depth"]

        same_domain = kwargs["same_domain"]
        base_domain = urlparse(seed_url).netloc.lower()

        frontier: deque[tuple[str, int]] = deque([(seed_url, 0)])
        discovered = {seed_url}
        pages: list[_CrawlPage] = []

        while frontier and len(pages) < max_pages:
            # 一次性取出当前所有任务
            current_level = [
                frontier.popleft()
                for _ in range(min(len(frontier), max_pages - len(pages)))
            ]
            level_pages: dict[str, _CrawlPage | None] = {}
            queue: asyncio.Queue[tuple[str, int]] = asyncio.Queue()
            for item in current_level:
                queue.put_nowait(item)

            async def worker(
                job_queue: asyncio.Queue[tuple[str, int]] = queue,
                result_pages: dict[str, _CrawlPage | None] = level_pages,
            ) -> None:
                while True:
                    url, depth = await job_queue.get()
                    try:
                        result_pages[url] = await self._fetch_page(url)
                    except Exception as exc:  # noqa: BLE001 - 单页失败必须隔离
                        warn("web_crawl 页面 worker 失败", url=url, depth=depth, e=exc)
                        result_pages[url] = None
                    finally:
                        job_queue.task_done()

            workers = [
                asyncio.create_task(worker())
                for _ in range(min(self._concurrency, len(current_level)))
            ]
            try:
                # 等待本层全部完成
                await queue.join()
            finally:
                for task in workers:
                    task.cancel()
                for task in workers:
                    with suppress(asyncio.CancelledError, Exception):
                        await task

            next_frontier: deque[tuple[str, int]] = deque()
            for url, depth in current_level:
                page = level_pages.get(url)
                if page is None:
                    continue
                pages.append(page)
                if len(pages) >= max_pages or depth >= max_depth or not page.raw_html:
                    continue

                for child_url in _extract_links(
                    page.raw_html,
                    base_url=url,
                    base_domain=base_domain,
                    same_domain=same_domain,
                ):
                    # 将未发现过的 url 入队
                    if child_url in discovered:
                        continue
                    try:
                        # child URL 是新的不可信输入，在进入队列前完成唯一一次校验。
                        validated_child = await validate_public_http_url_async(child_url)
                    except UrlSecurityError:
                        continue
                    discovered.add(validated_child)
                    next_frontier.append((validated_child, depth + 1))

            frontier.extend(next_frontier)

        if not pages:
            raise ToolExecutionError(
                reason="web_crawl_empty_result",
                detail_reason="No HTML pages could be crawled from the seed URL.",
                retryable=True,
            )

        return await self._build_output(pages, seed_url)


    async def _fetch_page(self, url: str) -> _CrawlPage | None:
        cached = await self._cache.read(url=url)
        if cached is not None:
            return _CrawlPage(
                source_url=url,
                text=cached.text,
                headers={},
                raw_html=cached.raw_html,
            )
        used_stealthy = False

        try:
            raw = await self._static_fetcher.fetch(url)
        except UrlFetchUnsupportedUrlError:
            return None
        except (UrlFetchError, UrlFetchHttpError) as exc:
            # 404、410 HTTP 错误不重试
            if isinstance(exc, UrlFetchHttpError) and exc.reason in _NOT_RETRYABLE_HTTP_REASONS:
                return None
            used_stealthy = True
            try:
                raw = await self._static_fetcher.fetch(url)
            except UrlFetchError:
                raw = None

        if raw is None or raw.raw_html is None:
            return None

        page, needs_fallback = await self._clean_html_page(raw)

        # static质量不足，则尝试stealthy
        if not used_stealthy and needs_fallback:
            try:
                fallback = await self._static_fetcher.fetch(url)
            except UrlFetchError:
                fallback = None
            if fallback is not None and fallback.raw_html is not None:
                raw = fallback
                page, needs_fallback = await self._clean_html_page(raw)

        if not page.text.strip():
            return None
        # 仅在内容充足的时候写缓存
        if not needs_fallback:
            await self._cache.write(
                url=url,
                headers=raw.headers,
                text=page.text,
                raw_html=raw.raw_html,
            )
        return page


    async def _clean_html_page(
        self,
        raw: RawFetchOutput,
    ) -> tuple[_CrawlPage, bool]:
        markdown = await asyncio.to_thread(
            clean_html,
            raw.raw_html or "",
            url=raw.source_url,
        )
        return (
            _CrawlPage(
                source_url=raw.source_url,
                text=markdown or "",
                headers=raw.headers,
                raw_html=raw.raw_html,
            ),
            should_fallback(
                raw=raw,
                markdown=markdown,
                min_text_length=self._min_text_length,
            ),
        )


    @staticmethod
    async def _build_output(pages: list[_CrawlPage], seed_url: str) -> dict[str, Any]:
        payload = {
            "seed_url": seed_url,
            "pages_crawled": len(pages),
            "pages": [
                {"url": page.source_url, "text": page.text}
                for page in pages
            ],
        }
        return payload


def _extract_links(
    raw_html: str,
    *,
    base_url: str,
    base_domain: str,
    same_domain: bool,
) -> list[str]:
    """从 HTML 中提取所有 a[href] 链接，进行绝对化、去重、协议/域名过滤 """
    try:
        hrefs = lxml_html.fromstring(raw_html).xpath("//a/@href")
    except Exception:  # noqa: BLE001 - 单页 HTML 解析失败只跳过链接发现
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
        if parsed.scheme.lower() not in {"http", "https"}:
            continue
        if same_domain and parsed.netloc.lower() != base_domain:
            continue
        if absolute in seen:
            continue
        seen.add(absolute)
        links.append(absolute)
    return links
