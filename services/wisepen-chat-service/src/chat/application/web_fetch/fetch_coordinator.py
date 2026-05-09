import time
from collections import OrderedDict
from typing import Optional, List, Tuple
from urllib.parse import urlparse

from chat.application.web_fetch.fetcher import StaticFetcher, SteelFetcher, SteelFetcherConfig, LocalScriptFetcher
from chat.application.web_fetch.content_processor import ContentProcessor
from common.logger import log_ok, log_fail

__all__ = [
    "FetchCoordinator",
]

CACHE_TTL_SECONDS = 10 * 60
CACHE_MAX_ITEMS = 128

DOCUMENT_EXTENSIONS = (
    ".pdf",
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
    ".ppt",
    ".pptx",
)


class FetchCoordinator:
    """网页抓取调度器：按优先级依次尝试多种抓取策略，自动降级

    抓取链路:
        普通模式 → StaticFetcher → SteelFetcher → LocalScriptFetcher
        强制浏览器 → SteelFetcher → LocalScriptFetcher

    每个抓取器返回的内容分为两类:
        - raw (str | bytes): 需要经过 ContentProcessor 处理
        - markdown (str): 已是干净的 Markdown，直接返回
    """

    def __init__(
        self,
        steel_base_url: str,
        min_content_length: int = 400,
        last_resort_min_length: int = 50,
        static_timeout: float = 15.0,
        browser_timeout: float = 60.0,
        cache_ttl_seconds: int = CACHE_TTL_SECONDS,
        cache_max_items: int = CACHE_MAX_ITEMS,
    ):
        self._min_content_length = min_content_length
        self._last_resort_min_length = last_resort_min_length
        self._cache_ttl_seconds = cache_ttl_seconds
        self._cache_max_items = cache_max_items
        self._cache: OrderedDict[tuple[str, bool], tuple[float, str]] = OrderedDict()

        self._static_fetcher = StaticFetcher(timeout=static_timeout)
        self._steel_fetcher = SteelFetcher(
            SteelFetcherConfig(base_url=steel_base_url, timeout=browser_timeout)
        )
        self._local_script_fetcher = LocalScriptFetcher(timeout=browser_timeout)

        self._processor = ContentProcessor(
            min_content_length=min_content_length,
            document_min_content_length=last_resort_min_length,
        )

        self._lightweight_chain: List[Tuple] = [
            (self._static_fetcher, "raw"),
            (self._steel_fetcher, "markdown"),
            (self._local_script_fetcher, "markdown"),
        ]

        self._browser_chain: List[Tuple] = [
            (self._steel_fetcher, "markdown"),
            (self._local_script_fetcher, "markdown"),
        ]

    async def fetch(self, url: str, *, force_browser: bool = False) -> Optional[str]:
        """从指定 URL 获取内容并转换为 Markdown

        Args:
            url: 目标网页 URL
            force_browser: 是否强制使用浏览器链路

        Returns:
            转换后的 Markdown 内容；全部失败则返回 None
        """
        url = url.strip()
        effective_force_browser = force_browser

        if force_browser and is_document_url(url):
            log_ok(
                "网页抓取文档 URL 忽略 force_browser，优先使用静态链路",
                url=url,
                force_browser=force_browser,
            )
            effective_force_browser = False

        cached = self._get_cached(url, effective_force_browser)
        if cached is not None:
            log_ok(
                "网页抓取缓存命中",
                url=url,
                force_browser=effective_force_browser,
                length=len(cached),
            )
            return cached

        chain = self._browser_chain if effective_force_browser else self._lightweight_chain
        failure_reasons: list[str] = []

        for i, (fetcher, content_type) in enumerate(chain):
            fetcher_name = fetcher.__class__.__name__
            is_last = i == len(chain) - 1
            min_length = self._last_resort_min_length if is_last else self._min_content_length

            try:
                content = await fetcher.fetch(url)
            except Exception as e:
                failure_reasons.append(f"{fetcher_name}: exception={e.__class__.__name__}")
                log_fail(
                    "网页抓取",
                    e,
                    url=url,
                    fetcher=fetcher_name,
                )
                continue

            if not content:
                failure_reasons.append(f"{fetcher_name}: empty content")
                log_fail(
                    "网页抓取",
                    "抓取内容为空",
                    url=url,
                    fetcher=fetcher_name,
                )
                continue

            if content_type == "markdown":
                markdown = str(content).strip()

                if len(markdown) < min_length:
                    failure_reasons.append(
                        f"{fetcher_name}: too short length={len(markdown)} min={min_length}"
                    )
                    log_fail(
                        "网页抓取",
                        f"内容过短({len(markdown)}字符)，阈值{min_length}，触发降级",
                        url=url,
                        fetcher=fetcher_name,
                    )
                    continue

                log_ok(
                    "网页抓取",
                    url=url,
                    fetcher=fetcher_name,
                    length=len(markdown),
                )
                self._set_cached(url, effective_force_browser, markdown)
                return markdown

            result = self._processor.process(content)
            if result is None:
                failure_reasons.append(f"{fetcher_name}: processor failed")
                log_fail(
                    "网页抓取",
                    "内容处理失败，触发降级",
                    url=url,
                    fetcher=fetcher_name,
                )
                continue

            log_ok(
                "网页抓取",
                url=url,
                fetcher=fetcher_name,
                length=len(result),
            )
            self._set_cached(url, effective_force_browser, result)
            return result

        log_fail(
            "网页抓取",
            "所有抓取器均失败",
            url=url,
            force_browser=effective_force_browser,
            reasons=" | ".join(failure_reasons[-5:]),
        )
        return None

    def _get_cached(self, url: str, force_browser: bool) -> Optional[str]:
        key = (url, force_browser)
        item = self._cache.get(key)

        if item is None:
            return None

        created_at, markdown = item
        age = time.monotonic() - created_at

        if age > self._cache_ttl_seconds:
            self._cache.pop(key, None)
            return None

        self._cache.move_to_end(key)
        return markdown

    def _set_cached(self, url: str, force_browser: bool, markdown: str) -> None:
        key = (url, force_browser)
        self._cache[key] = (time.monotonic(), markdown)
        self._cache.move_to_end(key)

        while len(self._cache) > self._cache_max_items:
            self._cache.popitem(last=False)


def is_document_url(url: str) -> bool:
    path = urlparse(url).path.lower()
    return path.endswith(DOCUMENT_EXTENSIONS)
