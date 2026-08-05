from __future__ import annotations

from chat.application.utils.url_security import UrlSecurityError
from chat.application.tools.web_tools.common import (
    WebContentCache,
    WebContentCacheRepository,
)
from common.logger import warn

from .batch_scheduler import FetchBatchScheduler, FetchJob
from .core.errors import (
    UrlFetchError,
    UrlFetchHttpError,
    UrlFetchUnsupportedUrlError,
)
from .core.models import (
    RawFetchOutput,
    WebFetchResult,
)
from .fetchers import WebFetcher
from .page_content import clean_html, extract_pdf_markdown, should_fallback


_NOT_RETRYABLE_HTTP_STATUS_REASONS = {
    "http 404",
    "http 410",
}


class FetchCoordinator:
    """协调缓存、静态抓取、浏览器回退、清洗和 PDF 提取。"""

    __slots__ = (
        "_batch_concurrency",
        "_cache",
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
        batch_concurrency: int = 16,
    ) -> None:
        self._static_fetcher = static_fetcher
        self._stealthy_fetcher = stealthy_fetcher
        self._cache = WebContentCache(
            repository=content_cache_repository,
        )
        self._min_text_length = min_text_length
        self._batch_concurrency = max(1, int(batch_concurrency))

    async def fetch(
        self,
        urls: list[str],
    ) -> tuple[WebFetchResult, ...]:
        if not urls:
            return ()

        async def run_static_job(
            job: FetchJob,
        ) -> tuple[WebFetchResult | None, bool]:
            # static 先查缓存；抓取失败或质量不足时允许进入 stealthy 阶段。
            return await self._run_fetch_job(
                job=job,
                fetcher=self._static_fetcher,
                read_cache=True,
                allow_fallback=True,
            )

        async def run_stealthy_job(
            job: FetchJob,
        ) -> tuple[WebFetchResult | None, bool]:
            # stealthy 是 fallback 的终点，不重复查缓存，也不再次触发 fallback。
            return await self._run_fetch_job(
                job=job,
                fetcher=self._stealthy_fetcher,
                read_cache=False,
                allow_fallback=False,
            )

        scheduler = FetchBatchScheduler(
            concurrency=self._batch_concurrency,
            static_job_handler=run_static_job,
            stealthy_job_handler=run_stealthy_job,
        )
        results = await scheduler.run(urls)

        return tuple(
            result
            for result in results
            if isinstance(result, WebFetchResult)
        )

    async def _run_fetch_job(
        self,
        *,
        job: FetchJob,
        fetcher: WebFetcher,
        read_cache: bool,
        allow_fallback: bool,
    ) -> tuple[WebFetchResult | None, bool]:
        try:
            if read_cache:
                cached = await self._cache.read(
                    url=job.url,
                )
                if cached is not None:
                    return WebFetchResult(
                        source_url=job.url,
                        text=cached.text,
                        is_md=cached.is_md,
                    ), False

            try:
                raw = await fetcher.fetch(job.url)
            except UrlFetchUnsupportedUrlError:
                return None, False
            except UrlFetchError as exc:
                should_fallback = allow_fallback and not (
                    isinstance(exc, UrlFetchHttpError)
                    and exc.reason in _NOT_RETRYABLE_HTTP_STATUS_REASONS
                )
                return None, should_fallback

            result, should_fallback = await self._build_result(raw)

            if should_fallback:
                # static 的低质量结果交给 scheduler 重新排入 stealthy；
                # stealthy 没有后续阶段，只能把当前结果直接交给调用方。
                return (result, False) if not allow_fallback else (None, True)

            await self._cache.write(
                url=job.url,
                headers=raw.headers,
                text=result.text,
                is_md=result.is_md,
                raw_html=raw.raw_html,
            )
            return result, False

        except UrlSecurityError:
            return None, False
        except UrlFetchError:
            return None, False
        except Exception as exc:
            warn(
                "网页抓取 worker 未预期失败",
                url=job.url,
                e=exc,
            )
            return None, False

    async def _build_result(
        self,
        raw: RawFetchOutput,
    ) -> tuple[WebFetchResult, bool]:
        if raw.pdf_bytes is not None:
            return (
                WebFetchResult(
                    source_url=raw.source_url,
                    text=await extract_pdf_markdown(raw.pdf_bytes, url=raw.source_url),
                    is_md=True,
                ),
                False,
            )

        markdown = clean_html(
            raw.raw_html or "",
            url=raw.source_url,
        )
        needs_fallback = should_fallback(
            raw=raw,
            markdown=markdown,
            min_text_length=self._min_text_length,
        )

        return (
            WebFetchResult(
                source_url=raw.source_url,
                text=markdown or "",
                is_md=True,
            ),
            needs_fallback,
        )
