from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path

from chat.application.tools.common.tool_run_file_store import ToolRunFileStore
from chat.application.tools.common.tool_run_file_store.errors import ToolRunFileStoreError
from common.logger import info, warn
from .cleaners.base import BaseCleaner
from .errors import WebFetchError
from .fetchers.base import BaseFetcher, RawFetchOutput
from .models import WebFetchBatchResult, WebFetchFailure, WebFetchResult
from .utils import filename_from_url, judge_quality

_PRODUCER_NAME = "web_fetch"


class FetchCoordinator:
    """单点抓取协调器：编排 httpx → scrapling fallback 链路 + 清洗 + 质量判断 + 文件移交。`WebCrawlService` 用于递归爬取。"""

    __slots__ = (
        "_httpx_fetcher",
        "_scrapling_fetcher",
        "_cleaner",
        "_file_store",
        "_min_text_length",
        "_batch_concurrency",
    )

    def __init__(
        self,
        *,
        httpx_fetcher: BaseFetcher,
        scrapling_fetcher: BaseFetcher,
        cleaner: BaseCleaner,
        file_store: ToolRunFileStore,
        min_text_length: int = 200,
        batch_concurrency: int = 5,
    ) -> None:
        self._httpx_fetcher = httpx_fetcher
        self._scrapling_fetcher = scrapling_fetcher
        self._cleaner = cleaner
        self._file_store = file_store
        self._min_text_length = min_text_length
        self._batch_concurrency = batch_concurrency

    async def fetch_one(
        self,
        url: str,
        *,
        user_id: str,
        session_id: str,
    ) -> WebFetchResult:
        """抓取单个 URL。

        强制走 httpx → scrapling 链路，不允许跳过。

        Args:
            url: 目标 URL。
            user_id: 用户隔离键（用于 ToolRunFileStore 文件移交）。
            session_id: 会话隔离键（用于 ToolRunFileStore 文件移交）。

        Returns:
            WebFetchResult: 成功结果（HTML 页面或非 HTML 文件引用）。

        Raises:
            WebFetchError: 抓取失败（HTTP 错误、网络错误、URL 不支持）。
        """
        info("web_fetch start", url=url)

        # 强制先走 httpx
        warnings: list[str] = []
        try:
            raw = await self._httpx_fetcher.fetch(url)
        except WebFetchError as exc:
            warn(
                "web_fetch httpx failed, falling back to scrapling",
                url=url,
                reason=exc.reason,
            )
            warnings.append(f"httpx_fallback: {exc.reason}")
            raw = await self._scrapling_fetcher.fetch(url)

        # 非 HTML 文件路径：移交 ToolRunFileStore
        if raw.file_path is not None:
            return await self._handle_non_html_file(
                raw=raw,
                user_id=user_id,
                session_id=session_id,
                warnings=warnings,
            )

        # HTML 路径：清洗 + 质量判断
        cleaned = self._cleaner.clean(raw.raw_html or "", url=raw.final_url or url)
        quality = judge_quality(
            raw=raw,
            cleaned=cleaned,
            min_text_length=self._min_text_length,
        )

        if quality.should_fallback:
            # httpx 质量不足，降级到 scrapling
            warn(
                "web_fetch httpx quality insufficient, falling back to scrapling",
                url=url,
                reason=quality.reason,
            )
            warnings.append(f"httpx_quality_fallback: {quality.reason}")
            raw = await self._scrapling_fetcher.fetch(url)
            # scrapling 只返回 HTML（非 HTML 已被 httpx 拦截），无需再判 file_path
            cleaned = self._cleaner.clean(
                raw.raw_html or "", url=raw.final_url or url
            )
            quality = judge_quality(
                raw=raw,
                cleaned=cleaned,
                min_text_length=self._min_text_length,
            )

        if quality.should_fallback:
            warnings.append(f"content quality insufficient: {quality.reason}")

        # 不截断 markdown：完整文本保留供后续缓存层处理
        return WebFetchResult(
            source_url=raw.source_url,
            final_url=raw.final_url,
            status_code=raw.status_code,
            content_type=raw.content_type,
            title=cleaned.title,
            markdown=cleaned.markdown,
            warnings=tuple(warnings),
        )

    async def fetch_many(
        self,
        urls: list[str],
        *,
        user_id: str,
        session_id: str,
    ) -> WebFetchBatchResult:
        """批量抓取 URL。

        单个 URL 失败不阻塞其他，转为 WebFetchFailure 加入 failed 列表。
        """
        semaphore = asyncio.Semaphore(self._batch_concurrency)

        async def _fetch_with_limit(u: str) -> WebFetchResult | WebFetchFailure:
            async with semaphore:
                try:
                    return await self.fetch_one(
                        u,
                        user_id=user_id,
                        session_id=session_id,
                    )
                except WebFetchError as exc:
                    return WebFetchFailure(
                        url=u,
                        reason=exc.reason,
                        detail=str(exc),
                    )

        results = await asyncio.gather(
            *[_fetch_with_limit(u) for u in urls]
        )

        items: list[WebFetchResult] = []
        failed: list[WebFetchFailure] = []
        for r in results:
            if isinstance(r, WebFetchResult):
                items.append(r)
            else:
                failed.append(r)

        batch_warnings: list[str] = []
        if failed:
            batch_warnings.append(f"{len(failed)}/{len(urls)} urls failed")

        return WebFetchBatchResult(
            items=tuple(items),
            failed=tuple(failed),
            warnings=tuple(batch_warnings),
        )

    async def _handle_non_html_file(
        self,
        *,
        raw: RawFetchOutput,
        user_id: str,
        session_id: str,
        warnings: list[str],
    ) -> WebFetchResult:
        """移交非 HTML 文件到 ToolRunFileStore，返回带 file_ref 的结果。"""
        file_path = raw.file_path
        assert file_path is not None  # 由调用方保证

        try:
            filename = filename_from_url(raw.final_url or raw.source_url) or f"download.{raw.file_label or 'bin'}"
            record = await self._file_store.publish_file(
                user_id=user_id,
                session_id=session_id,
                producer=_PRODUCER_NAME,
                path=file_path,
                filename=filename,
                content_type=raw.content_type,
                ref_prefix="web",
            )
            info(
                "web_fetch non-html file published",
                url=raw.source_url,
                ref_id=record.ref_id,
                label=raw.file_label,
            )
            return WebFetchResult(
                source_url=raw.source_url,
                final_url=raw.final_url,
                status_code=raw.status_code,
                content_type=raw.content_type,
                title=None,
                markdown=None,
                warnings=tuple(warnings),
                file_ref=record.ref_id,
                file_label=raw.file_label,
            )
        except ToolRunFileStoreError as exc:
            raise WebFetchError(
                url=raw.source_url,
                reason=f"file_publish_failed: {exc}",
            ) from exc
        finally:
            # 清理临时文件
            with contextlib.suppress(OSError):
                Path(file_path).unlink(missing_ok=True)
