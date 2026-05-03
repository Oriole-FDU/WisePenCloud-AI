from typing import Optional, List, Tuple
from chat.application.web_fetch.fetcher import StaticFetcher, SteelFetcher, LocalScriptFetcher
from chat.application.web_fetch.content_processor import ContentProcessor
from common.logger import log_ok, log_fail


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
    ):
        self._min_content_length = min_content_length
        self._last_resort_min_length = last_resort_min_length
        self._static_fetcher = StaticFetcher(timeout=static_timeout)
        self._steel_fetcher = SteelFetcher(steel_base_url=steel_base_url, timeout=browser_timeout)
        self._local_script_fetcher = LocalScriptFetcher(timeout=browser_timeout)
        self._processor = ContentProcessor(min_content_length=min_content_length)

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
        chain = self._browser_chain if force_browser else self._lightweight_chain

        for i, (fetcher, content_type) in enumerate(chain):
            fetcher_name = fetcher.__class__.__name__
            is_last = i == len(chain) - 1
            min_length = self._last_resort_min_length if is_last else self._min_content_length

            try:
                content = await fetcher.fetch(url)
            except Exception as e:
                log_fail("网页抓取", e, url=url, fetcher=fetcher_name)
                continue

            if not content:
                log_fail("网页抓取", "抓取内容为空", url=url, fetcher=fetcher_name)
                continue

            if content_type == "markdown":
                if len(content.strip()) < min_length:
                    log_fail("网页抓取", "内容过短，触发降级", url=url, fetcher=fetcher_name)
                    continue
                log_ok("网页抓取", url=url, fetcher=fetcher_name)
                return content.strip()

            result = self._processor.process(content)
            if result is None:
                log_fail("网页抓取", "内容处理失败，触发降级", url=url, fetcher=fetcher_name)
                continue

            log_ok("网页抓取", url=url, fetcher=fetcher_name)
            return result

        log_fail("网页抓取", "所有抓取器均失败", url=url)
        return None
