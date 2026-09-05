from __future__ import annotations

import asyncio
from collections import deque
from contextlib import suppress
from dataclasses import dataclass
from typing import Any

from common.logger import warn

from chat.application.tools.core import (
    ToolDefinition,
    ToolLLMSpec,
    ToolParametersSchema,
    ToolPolicy,
    ToolRiskLevel,
)
from chat.application.tools.core.output_cache import cacheable_tool_output

from .common import UrlSecurityError, WebContentCache, validate_public_http_url_async
from .fetchers import (
    RawFetchOutput,
    UrlFetchError,
    UrlFetchHttpError,
    UrlFetchUnsupportedUrlError,
    WebFetcher,
)
from .page_content import clean_html, extract_pdf_markdown, should_fallback

MAX_URLS = 64
DEFAULT_CONCURRENCY = 16
DEFAULT_MIN_TEXT_LENGTH = 200
_NOT_RETRYABLE_HTTP_REASONS = {"http 404", "http 410"}

_PARAMETERS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "urls": {
            "type": "array",
            "items": {"type": "string", "minLength": 1},
            "minItems": 1,
            "maxItems": MAX_URLS,
            "description": (
                "One or more complete public http:// or https:// URLs. HTML pages are "
                "cleaned to Markdown and direct PDFs use fast native text extraction. "
                "Do not pass a search query, site name, or relative URL."
            ),
        },
    },
    "required": ["urls"],
    "additionalProperties": False,
}


@dataclass(frozen=True, slots=True)
class _FetchPage:
    source_url: str
    text: str
    headers: dict[str, str]
    raw_html: str | None = None


@dataclass(frozen=True, slots=True)
class _FetchJob:
    index: int
    url: str


class WebFetchTool:
    """直接拥有 URL 校验、缓存、两阶段抓取和结果投影。"""

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
                name="web_fetch",
                description=(
                    "Fetch one or more specific public HTTP(S) URLs and return readable "
                    "content. Use this when the exact URLs are known, including several "
                    "unrelated pages. HTML and direct PDFs are returned as Markdown. "
                    "Invalid or unsupported URLs are omitted. Use web_crawl when linked "
                    "pages must be discovered from a seed URL."
                ),
                parameters_schema=ToolParametersSchema(_PARAMETERS_SCHEMA),
            ),
            policy=ToolPolicy(
                expose_by_default=True,
                persist_output=True,
                risk_level=ToolRiskLevel.MEDIUM,
                timeout_seconds=300.0,
                # 超预算正文由 claim-check 装饰器转存并展示 preview/receipt，短正文保持原输出。
                max_output_chars=None,
            ),
        )

    @property
    def definition(self) -> ToolDefinition:
        return self._definition

    @cacheable_tool_output(paths=("items.*.text",))
    async def execute(
        self,
        context: dict[str, Any],
        config: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        del context, config

        # 构建任务队列
        raw_urls = kwargs["urls"]
        jobs: list[_FetchJob] = []
        for index, raw_url in enumerate(raw_urls):
            url = raw_url.strip()
            try:
                # 工具入口完成一次安全校验；后续 fetch/cache 只接收已校验 URL。
                validated_url = await validate_public_http_url_async(url)
            except UrlSecurityError as exc:
                warn("web_fetch URL 被安全策略跳过", url=url, reason=str(exc))
                continue
            jobs.append(_FetchJob(index=index, url=validated_url))

        if not jobs:
            return self._build_output((), warning="all_urls_invalid")

        # 并发执行所有任务

        pages: list[_FetchPage | None] = [None] * len(raw_urls)

        # 两个队列共同占据并发槽位，static -> stealthy
        static_queue: deque[_FetchJob] = deque(jobs)
        stealthy_queue: deque[_FetchJob] = deque()

        # 活跃任务字典: Task -> (job, is_stealthy)
        active_tasks: dict[asyncio.Task, tuple[_FetchJob, bool]] = {}

        try:
            # 逐个加入队列，直到达到槽位上限
            while static_queue or stealthy_queue or active_tasks:
                while len(active_tasks) < self._concurrency and (static_queue or stealthy_queue):
                    if static_queue:
                        job = static_queue.popleft()
                        task = asyncio.create_task(self._run_static_job(job))
                        active_tasks[task] = (job, False)
                    else:
                        job = stealthy_queue.popleft()
                        task = asyncio.create_task(self._run_stealthy_job(job))
                        active_tasks[task] = (job, True)
                # 如果没有活跃任务，不进入后续处理
                if not active_tasks:
                    continue
                # 处理本次处理完成的任务并移出任务表
                completed, _ = await asyncio.wait(
                    active_tasks,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for task in completed:
                    job, is_stealthy = active_tasks.pop(task)
                    try:
                        page, retry_with_stealthy = task.result()
                    except Exception as exc:  # noqa: BLE001 - 单个 URL 失败不影响同批任务
                        warn("web_fetch worker 未预期失败", url=job.url, e=exc)
                        continue
                    # 按照原始顺序索引放置，确保按照输入顺序返回
                    pages[job.index] = page
                    # 加入 stealthy 队列重试
                    if not is_stealthy and retry_with_stealthy:
                        stealthy_queue.append(job)
        finally:
            for task in active_tasks:
                task.cancel()
            for task in active_tasks:
                with suppress(asyncio.CancelledError, Exception):
                    await task

        successful_pages = tuple(page for page in pages if page is not None)
        return self._build_output(
            successful_pages,
            warning="no_results" if not successful_pages else None,
        )


    async def _run_static_job(
        self,
        job: _FetchJob,
    ) -> tuple[_FetchPage | None, bool]:
        cached = await self._cache.read(url=job.url)
        if cached is not None:
            return (
                _FetchPage(
                    source_url=job.url,
                    text=cached.text,
                    headers={},
                    raw_html=cached.raw_html,
                ),
                False,
            )

        try:
            raw = await self._static_fetcher.fetch(job.url)
        except UrlFetchUnsupportedUrlError:
            return None, False
        except UrlFetchHttpError as exc:
            return None, exc.reason not in _NOT_RETRYABLE_HTTP_REASONS
        except UrlFetchError:
            return None, True

        try:
            page, needs_fallback = await self._build_page(raw)
        except UrlFetchError:
            return None, False
        if needs_fallback:
            return None, True
        await self._cache.write(
            url=job.url,
            headers=raw.headers,
            text=page.text,
            raw_html=page.raw_html,
        )
        return page, False


    async def _run_stealthy_job(
        self,
        job: _FetchJob,
    ) -> tuple[_FetchPage | None, bool]:
        try:
            raw = await self._stealthy_fetcher.fetch(job.url)
            page, needs_fallback = await self._build_page(raw)
        except UrlFetchError:
            return None, False
        if not page.text.strip():
            return None, False
        if not needs_fallback:
            await self._cache.write(
                url=job.url,
                headers=raw.headers,
                text=page.text,
                raw_html=page.raw_html,
            )
        return page, False


    async def _build_page(self, raw: RawFetchOutput) -> tuple[_FetchPage, bool]:
        if raw.pdf_bytes is not None:
            text = await extract_pdf_markdown(raw.pdf_bytes, url=raw.source_url)
            return (
                _FetchPage(
                    source_url=raw.source_url,
                    text=text,
                    headers=raw.headers,
                ),
                False,
            )
        markdown = await asyncio.to_thread(
            clean_html,
            raw.raw_html or "",
            url=raw.source_url,
        )
        return (
            _FetchPage(
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
    def _build_output(
        pages: tuple[_FetchPage, ...],
        *,
        warning: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "items": [
                {"source_url": page.source_url, "text": page.text}
                for page in pages
            ],
        }
        if warning:
            payload["warning"] = warning
        return payload
