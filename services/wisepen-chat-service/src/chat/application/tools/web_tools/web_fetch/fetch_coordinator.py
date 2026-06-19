from __future__ import annotations

import asyncio
import contextlib
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path

from chat.application.tools.common.tool_run_file_store import ToolRunFileStore
from chat.application.tools.common.tool_run_file_store.errors import ToolRunFileStoreError
from chat.application.tools.web_tools.web_content_cache.models import (
    WebContentCacheEntry,
    WebContentCacheMode,
    WebContentCacheValue,
)
from chat.application.tools.web_tools.web_content_cache.repository import (
    WebContentCacheRepository,
)
from chat.application.tools.web_tools.web_content_cache.refresh_queue import (
    WEB_FETCH_REFRESH_JOB,
    WebContentCacheRefreshJob,
    WebContentCacheRefreshTaskPublisher,
)
from chat.application.tools.web_tools.web_content_cache.service import (
    HtmlCacheWrite,
    WebContentCacheService,
)
from common.logger import info, warn
from chat.application.tools.web_tools.web_content_cache.cache_ttl import compute_ttl
from .cleaners.base import BaseCleaner
from .errors import WebFetchError
from .fetchers.base import BaseFetcher, RawFetchOutput
from .models import WebFetchBatchResult, WebFetchFailure, WebFetchResult
from .utils import filename_from_url, judge_quality

_PRODUCER_NAME = "web_fetch"
_REFRESH_LOCK_TTL_SECONDS = 300


class FetchCoordinator:
    """单点抓取协调器：编排 httpx → scrapling fallback 链路 + 清洗 + 质量判断 + 文件移交。`WebCrawlService` 用于递归爬取。"""

    __slots__ = (
        "_httpx_fetcher",
        "_scrapling_fetcher",
        "_cleaner",
        "_content_cache_service",
        "_content_cache_repository",
        "_refresh_task_publisher",
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
        content_cache_repository: WebContentCacheRepository | None = None,
        refresh_task_publisher: WebContentCacheRefreshTaskPublisher | None = None,
        min_text_length: int = 200,
        batch_concurrency: int = 5,
    ) -> None:
        self._httpx_fetcher = httpx_fetcher
        self._scrapling_fetcher = scrapling_fetcher
        self._cleaner = cleaner
        self._file_store = file_store
        self._content_cache_repository = content_cache_repository
        self._refresh_task_publisher = refresh_task_publisher
        self._content_cache_service = WebContentCacheService(
            repository=content_cache_repository,
            refresh_task_publisher=refresh_task_publisher,
        )
        self._min_text_length = min_text_length
        self._batch_concurrency = batch_concurrency

    async def fetch_one(
        self,
        url: str,
        *,
        user_id: str,
        session_id: str,
        source_scope: str = "web_public",
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
        info("网页抓取开始", url=url)

        cached_result = await self._read_cached_result(
            url=url,
            user_id=user_id,
            session_id=session_id,
            source_scope=source_scope,
        )
        if cached_result is not None:
            return cached_result

        # 强制先走 httpx
        warnings: list[str] = []
        try:
            raw = await self._httpx_fetcher.fetch(url)
        except WebFetchError as exc:
            warn(
                "网页抓取 httpx 失败，降级到 scrapling",
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
                source_scope=source_scope,
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
                "网页抓取 httpx 内容质量不足，降级到 scrapling",
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
        result = WebFetchResult(
            source_url=raw.source_url,
            final_url=raw.final_url,
            status_code=raw.status_code,
            content_type=raw.content_type,
            title=cleaned.title,
            markdown=cleaned.markdown,
            warnings=tuple(warnings),
            source_scope=source_scope,
        )
        if not quality.should_fallback:
            await self._write_html_cache(
                url=url,
                user_id=user_id,
                source_scope=source_scope,
                raw=raw,
                result=result,
            )
        return result

    async def fetch_many(
        self,
        urls: list[str],
        *,
        user_id: str,
        session_id: str,
        source_scope: str = "web_public",
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
                        source_scope=source_scope,
                    )
                except WebFetchError as exc:
                    return WebFetchFailure(
                        url=u,
                        reason=exc.reason,
                        detail=str(exc),
                    )

        results = await asyncio.gather(*[_fetch_with_limit(u) for u in urls])

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
        source_scope: str,
        warnings: list[str],
    ) -> WebFetchResult:
        """移交非 HTML 文件到 ToolRunFileStore，返回带 file_ref 的结果。"""
        file_path = raw.file_path
        assert file_path is not None  # 由调用方保证

        try:
            filename = (
                filename_from_url(raw.final_url or raw.source_url)
                or f"download.{raw.file_label or 'bin'}"
            )
            cache_doc_id = await self._write_non_html_cache_stub(
                user_id=user_id,
                source_scope=source_scope,
                raw=raw,
            )
            record = await self._file_store.publish_file(
                user_id=user_id,
                session_id=session_id,
                producer=_PRODUCER_NAME,
                path=file_path,
                filename=filename,
                content_type=raw.content_type,
                ref_prefix=source_scope,
                metadata={
                    "source_kind": "web_fetch",
                    "source_scope": source_scope,
                    "source_url": raw.source_url,
                    "final_url": raw.final_url,
                    "content_type": raw.content_type,
                    "source_cache_doc_id": cache_doc_id,
                },
            )
            info(
                "网页抓取非 HTML 文件已发布为临时文件引用",
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
                source_scope=source_scope,
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

    async def _read_cached_result(
        self,
        *,
        url: str,
        user_id: str,
        session_id: str,
        source_scope: str,
    ) -> WebFetchResult | None:
        """读取未过期的网页内容缓存快照（优先 PRIVATE，次选 PUBLIC）。"""
        repository = self._content_cache_repository
        if repository is None:
            return None

        try:
            now = datetime.now(timezone.utc)
            # 同一用户优先读自己的 private 缓存；没有可用 private 时再回落 public。
            for mode in (WebContentCacheMode.PRIVATE, WebContentCacheMode.PUBLIC):
                entry = await repository.get_entry(
                    user_id=user_id,
                    url=url,
                    cache_mode=mode,
                )
                if entry is None:
                    continue

                value = await repository.get_value(doc_id=entry.mongo_doc_id)
                # 实体缺失或正文为空则跳过
                if value is None or not value.markdown:
                    continue

                soft_expire_at = entry.soft_expire_at
                if soft_expire_at.tzinfo is None:
                    soft_expire_at = soft_expire_at.replace(tzinfo=timezone.utc)

                hard_expire_at = entry.hard_expire_at
                if hard_expire_at.tzinfo is None:
                    hard_expire_at = hard_expire_at.replace(tzinfo=timezone.utc)

                if now > hard_expire_at:
                    continue

                title = value.metadata.get("title")
                cached_result = WebFetchResult(
                    source_url=url,
                    final_url=value.final_url,
                    status_code=value.status_code,
                    content_type=value.content_type,
                    title=title if isinstance(title, str) else None,
                    markdown=value.markdown,
                    source_scope=source_scope,
                )

                if now > soft_expire_at:
                    refresh_source_scope = (
                        "web_custom"
                        if entry.cache_mode == WebContentCacheMode.PRIVATE
                        else "web_public"
                    )
                    await self._content_cache_service.schedule_stale_refresh(
                        url=url,
                        user_id=user_id,
                        session_id=session_id,
                        source_scope=refresh_source_scope,
                        cache_mode=entry.cache_mode,
                        refresh_job_prefix="web_fetch",
                        refresh_task_name=WEB_FETCH_REFRESH_JOB,
                        refresh_lock_ttl_seconds=_REFRESH_LOCK_TTL_SECONDS,
                    )
                    info(
                        "网页抓取命中 stale 缓存",
                        url=url,
                        cache_mode=entry.cache_mode.value,
                        doc_id=entry.mongo_doc_id,
                    )
                    return cached_result

                info(
                    "网页抓取命中新鲜缓存",
                    url=url,
                    cache_mode=entry.cache_mode.value,
                    doc_id=entry.mongo_doc_id,
                )
                return cached_result
        except Exception as exc:
            # 容灾：缓存读取异常时降级平滑退化至物理网络抓取
            warn("网页抓取缓存读取失败，降级为实时抓取", url=url, e=exc)

        return None

    async def refresh_stale_url(
        self,
        *,
        url: str,
        user_id: str,
        source_scope: str,
    ) -> None:
        try:
            raw = await self._httpx_fetcher.fetch(url)
            if raw.file_path is not None:
                with contextlib.suppress(OSError):
                    Path(raw.file_path).unlink(missing_ok=True)
                await self._write_non_html_cache_stub(
                    user_id=user_id,
                    source_scope=source_scope,
                    raw=raw,
                )
                return

            cleaned = self._cleaner.clean(raw.raw_html or "", url=raw.final_url or url)
            quality = judge_quality(
                raw=raw,
                cleaned=cleaned,
                min_text_length=self._min_text_length,
            )
            if quality.should_fallback:
                raw = await self._scrapling_fetcher.fetch(url)
                cleaned = self._cleaner.clean(raw.raw_html or "", url=raw.final_url or url)
                quality = judge_quality(
                    raw=raw,
                    cleaned=cleaned,
                    min_text_length=self._min_text_length,
                )

            if quality.should_fallback:
                return

            await self._write_html_cache(
                url=url,
                user_id=user_id,
                source_scope=source_scope,
                raw=raw,
                result=WebFetchResult(
                    source_url=raw.source_url,
                    final_url=raw.final_url,
                    status_code=raw.status_code,
                    content_type=raw.content_type,
                    title=cleaned.title,
                    markdown=cleaned.markdown,
                    source_scope=source_scope,
                ),
            )
        except Exception as exc:
            warn("网页抓取 stale 后台刷新失败", url=url, e=exc)

    async def _write_html_cache(
        self,
        *,
        url: str,
        user_id: str,
        source_scope: str,
        raw: RawFetchOutput,
        result: WebFetchResult,
    ) -> None:
        """写入 HTML 清洗结果缓存；写失败不影响本次抓取结果。"""
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
                producer=_PRODUCER_NAME,
            )
        )

    async def _write_non_html_cache_stub(
        self,
        *,
        user_id: str,
        source_scope: str,
        raw: RawFetchOutput,
    ) -> str | None:
        """为后续 document_parse 回写 markdown 预创建同一 URL 缓存文档。"""
        repository = self._content_cache_repository
        if repository is None:
            return None

        try:
            now = datetime.now(timezone.utc)
            # 非 HTML 先写占位文档，document_parse 后续按同一访问域回填 markdown。
            mode = (
                WebContentCacheMode.PRIVATE
                if source_scope == "web_custom"
                else WebContentCacheMode.PUBLIC
            )
            # 基于 HTTP 响应头智能计算 TTL
            ttl = compute_ttl(
                headers=raw.headers,
                now=now,
                is_shared_cache=(mode == WebContentCacheMode.PUBLIC),
                status_code=raw.status_code or 200,
            )
            if ttl.no_store:
                info("网页抓取非 HTML 缓存被 no-store 指令跳过", url=raw.source_url)
                return None
            canonical_url = raw.source_url.strip()
            value = WebContentCacheValue(
                id=None,
                user_id=user_id,
                canonical_url=canonical_url,
                final_url=raw.final_url,
                cache_mode=mode,
                status_code=raw.status_code,
                content_type=raw.content_type,
                raw_html=None,
                markdown=None,
                fetched_at=now,
                metadata={
                    "source_scope": source_scope,
                    "source_url": raw.source_url,
                    "fetcher": raw.fetcher,
                    "file_label": raw.file_label,
                    "cache_control": raw.headers.get("cache-control"),
                },
            )
            doc_id = await repository.save_value(value)
            await repository.set_entry(
                WebContentCacheEntry(
                    user_id=user_id,
                    url_hash=sha256(canonical_url.encode("utf-8")).hexdigest(),
                    canonical_url=canonical_url,
                    mongo_doc_id=doc_id,
                    cache_mode=mode,
                    soft_expire_at=ttl.soft_expire_at,
                    hard_expire_at=ttl.hard_expire_at,
                    etag=raw.headers.get("etag"),
                    last_modified=raw.headers.get("last-modified"),
                )
            )
            return doc_id
        except Exception as exc:
            warn("网页抓取非 HTML 缓存占位文档写入失败", url=raw.source_url, e=exc)
            return None
