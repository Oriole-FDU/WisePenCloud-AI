import asyncio
from typing import List, Optional

from cachetools import TTLCache

from chat.application.security.url_security import (
    UrlSecurityError,
    validate_public_http_url,
)
from chat.application.tools.web.services.web_fetch.enums import FetcherName
from chat.application.tools.web.services.web_fetch.errors import UnsupportedMediaError
from chat.application.tools.web.services.web_fetch.fetcher.base import BaseFetcher
from chat.application.tools.web.services.web_fetch.models import (
    FetchedDocument,
    FetchedPage,
    FetchedRedirect,
    FetchResultItem,
)
from chat.application.tools.web.utils.domains import extract_domain
from common.logger import log_error, log_event, log_fail

# 批量抓取并发限制信号量阈值
_BATCH_CONCURRENCY = 5
_DOCUMENT_URL_EXTENSIONS = (
    ".pdf",
    ".docx",
    ".docm",
    ".pptx",
    ".pptm",
    ".xls",
    ".xlsx",
    ".xlsm",
    ".ods",
    ".epub",
)


class FetchCoordinator:
    """Web 抓取固定降级链调度器，按 static -> steel -> local_js 链路顺序调度。"""

    def __init__(
        self,
        static_fetcher: BaseFetcher,
        steel_fetcher: BaseFetcher,
        local_fetcher: BaseFetcher,
        min_content_length: int,
        last_resort_min_length: int,
        cache_ttl_seconds: int,
        cache_max_items: int,
    ):
        """注入三个降级抓取器及缓存配置参数。"""
        self._static_fetcher = static_fetcher
        self._steel_fetcher = steel_fetcher
        self._local_fetcher = local_fetcher

        self._min_content_length = min_content_length
        self._last_resort_min_length = last_resort_min_length

        # 初始化成功结果的内存缓存
        self._cache: TTLCache[str, FetchedPage] = TTLCache(
            maxsize=cache_max_items,
            ttl=cache_ttl_seconds,
        )

    async def _fetch_one(self, url: str) -> FetchResultItem:
        """对单个 URL 执行完整的缓存校验、安全审计及降级链抓取逻辑。"""
        original_url = url
        # 1. 安全校验
        try:
            url = await asyncio.to_thread(validate_public_http_url, url)
        except UrlSecurityError as e:
            log_fail(
                "web_fetch url security",
                e,
                url=original_url,
            )
            return FetchResultItem(
                url=url,
                success=False,
                error=f"URL rejected by security policy: {e}",
            )

        # 2. 命中缓存直接返回
        self._cache.expire()
        cached = self._cache.get(url)
        if cached is not None:
            log_event(
                "web_fetch fetcher cache hit",
                url=url,
                status_code=cached.status_code,
                markdown_length=len(cached.markdown or ""),
            )
            return FetchResultItem(
                url=url,
                success=True,
                content=cached.markdown,
                links=cached.links,
                title=cached.title,
                final_url=cached.final_url,
                domain=cached.domain,
                status_code=cached.status_code,
                fetcher=FetcherName.CACHE,
            )

        # 3. 尝试第一层：静态抓取
        static_result = await self._fetch_with_threshold(
            fetcher=self._static_fetcher,
            url=url,
            min_content_length=self._min_content_length,
        )
        if static_result is not None:
            return static_result

        # 4. 文档直链失败截断：若是文档后缀则不再传给浏览器抓取器
        document_url = url.lower().split("?", 1)[0]
        if any(
            document_url.endswith(extension)
            for extension in _DOCUMENT_URL_EXTENSIONS
        ):
            log_fail(
                "web_fetch document fetch",
                "static fetch failed; browser fallback skipped for document URL",
                url=url,
            )
            return FetchResultItem(
                url=url,
                success=False,
                error="static fetch failed for document URL",
            )

        # 5. 尝试第二层：Steel 浏览器重度抓取
        steel_result = await self._fetch_with_threshold(
            fetcher=self._steel_fetcher,
            url=url,
            min_content_length=self._min_content_length,
        )
        if steel_result is not None:
            return steel_result

        # 6. 尝试第三层：本地脚本浏览器渲染兜底
        local_result = await self._fetch_with_threshold(
            fetcher=self._local_fetcher,
            url=url,
            min_content_length=self._last_resort_min_length,
        )
        if local_result is not None:
            return local_result

        log_fail(
            "web_fetch fetcher chain",
            "all fetchers failed or returned insufficient content",
            url=url,
        )
        return FetchResultItem(
            url=url,
            success=False,
            error="所有 fetcher 均失败",
        )

    async def _fetch_with_threshold(
        self,
        *,
        fetcher: BaseFetcher,
        url: str,
        min_content_length: int,
    ) -> Optional[FetchResultItem]:
        """执行指定 Fetcher 抓取，并对返回内容做形态分类和长度门槛校验。"""
        fetcher_name = fetcher.name
        log_event(
            "web_fetch fetcher start",
            fetcher=fetcher_name,
            url=url,
            min_content_length=min_content_length,
        )

        try:
            content = await fetcher.fetch(url)
        except UnsupportedMediaError as e:
            log_fail(
                "web_fetch fetcher unsupported media",
                e,
                fetcher=fetcher_name,
                url=url,
            )
            return FetchResultItem(
                url=url, success=False, error=str(e), fetcher=fetcher_name
            )
        except UrlSecurityError as e:
            log_fail(
                "web_fetch fetcher url security",
                e,
                fetcher=fetcher_name,
                url=url,
            )
            return FetchResultItem(
                url=url,
                success=False,
                error=f"URL rejected by security policy: {e}",
                fetcher=fetcher_name,
            )
        except Exception as e:
            log_error(
                "web_fetch fetcher unexpected",
                e,
                fetcher=fetcher_name,
                url=url,
            )
            return None

        if not content:
            log_fail(
                "web_fetch fetcher result",
                "empty result",
                fetcher=fetcher_name,
                url=url,
            )
            return None

        # 文档类型结果直接放行
        if isinstance(content, FetchedDocument):
            log_event(
                "web_fetch fetcher success",
                fetcher=fetcher_name,
                url=url,
                content_type="document",
                media_type=content.media_type,
                size_bytes=len(content.content or b""),
            )
            return FetchResultItem(
                url=url,
                success=True,
                document=content,
                fetcher=fetcher_name,
            )

        # 显式拦截 3xx 阶段检测到的跨站重定向
        if isinstance(content, FetchedRedirect):
            log_fail(
                "web_fetch fetcher redirect",
                "cross-host redirect before content fetch",
                fetcher=fetcher_name,
                url=url,
                status_code=content.status_code,
                redirect_url=content.redirect_url,
            )
            return FetchResultItem(
                url=url,
                success=False,
                status_code=content.status_code,
                redirect_url=content.redirect_url,
                error="Cross-host redirect requires explicit web_fetch call.",
                fetcher=fetcher_name,
            )

        markdown = content.markdown.strip()
        markdown_length = len(markdown)

        # 拦截跟随跳转后最终状态的跨站重定向
        if content.final_url and extract_domain(url) != extract_domain(content.final_url):
            log_fail(
                "web_fetch fetcher redirect",
                "cross-host final URL",
                fetcher=fetcher_name,
                url=url,
                final_url=content.final_url,
                status_code=content.status_code,
                markdown_length=markdown_length,
            )
            return FetchResultItem(
                url=url,
                success=False,
                title=content.title,
                final_url=content.final_url,
                domain=content.domain,
                status_code=content.status_code,
                redirect_url=content.final_url,
                error="Cross-host redirect requires explicit web_fetch call.",
                fetcher=fetcher_name,
            )

        # 验证文本长度门槛，不足则判定当前 fetcher 失败以触发降级
        if markdown_length < min_content_length:
            log_fail(
                "web_fetch fetcher result",
                "content below threshold",
                fetcher=fetcher_name,
                url=url,
                status_code=content.status_code,
                markdown_length=markdown_length,
                min_content_length=min_content_length,
                title=content.title,
                final_url=content.final_url,
            )
            return None

        # 写入缓存并组装成功响应
        self._cache.expire()
        self._cache[url] = FetchedPage(
            markdown=markdown,
            links=content.links,
            title=content.title,
            final_url=content.final_url,
            domain=content.domain,
            status_code=content.status_code,
        )

        log_event(
            "web_fetch fetcher success",
            fetcher=fetcher_name,
            url=url,
            status_code=content.status_code,
            markdown_length=markdown_length,
            title=content.title,
            final_url=content.final_url,
        )
        return FetchResultItem(
            url=url,
            success=True,
            content=markdown,
            links=content.links,
            title=content.title,
            final_url=content.final_url,
            domain=content.domain,
            status_code=content.status_code,
            fetcher=fetcher_name,
        )

    async def fetch_many(self, urls: List[str]) -> List[FetchResultItem]:
        """批量并发抓取多个 URL，通过 Semaphore 控制并发上限。"""
        semaphore = asyncio.Semaphore(_BATCH_CONCURRENCY)

        async def fetch_index(url: str) -> FetchResultItem:
            """在信号量保护下执行单个 URL 的完整抓取流程。"""
            async with semaphore:
                try:
                    return await self._fetch_one(url)
                except Exception as e:
                    return FetchResultItem(
                        url=url,
                        success=False,
                        error=f"未预期异常: {e.__class__.__name__}",
                    )

        return list(
            await asyncio.gather(*(fetch_index(url) for url in urls))
        )
