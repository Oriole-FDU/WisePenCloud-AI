from typing import Optional, List, Tuple, Literal

from chat.application.browse_url.fetcher import StaticFetcher, SteelFetcher, LocalScriptFetcher
from chat.application.browse_url.cleaner import extract_main_content, convert_to_markdown
from common.logger import log_ok, log_fail


class SmartFetcher:
    """网页抓取调度器：按优先级依次尝试多种抓取策略，自动降级

    抓取链路:
        auto 模式    →  StaticFetcher → SteelFetcher → LocalScriptFetcher
        browser 模式 →  SteelFetcher → LocalScriptFetcher

    每个抓取器返回的内容分为两类:
        - html:    需要经过 readability 提取 + markdownify 转换
        - markdown: 已是干净的 Markdown，直接返回
    """

    def __init__(self, steel_base_url: str, min_content_length: int = 400):
        self._static_fetcher = StaticFetcher()
        self._steel_fetcher = SteelFetcher(steel_base_url=steel_base_url)
        self._local_script_fetcher = LocalScriptFetcher()
        self._min_content_length = min_content_length

        self._whole_chain: List[Tuple] = [
            (self._static_fetcher, "html"),
            (self._steel_fetcher, "html"),
            (self._local_script_fetcher, "markdown"),
        ]

        self._browser_chain: List[Tuple] = [
            (self._steel_fetcher, "html"),
            (self._local_script_fetcher, "markdown"),
        ]

    async def fetch(self, url: str, mode: Literal["auto", "browser"] = "auto") -> Optional[str]:
        """从指定 URL 获取内容并转换为 Markdown

        Args:
            url:  目标网页 URL
            mode: 抓取模式
                - "auto":    全链路尝试（静态 → Steel → 本地浏览器）
                - "browser": 仅浏览器链路（Steel → 本地浏览器）

        Returns:
            转换后的 Markdown 内容；全部失败则返回 None
        """
        chain = self._whole_chain if mode == "auto" else self._browser_chain

        for fetcher, content_type in chain:
            fetcher_name = fetcher.__class__.__name__

            try:
                content = await fetcher.fetch(url)
            except Exception as e:
                log_fail("网页抓取", e, url=url, fetcher=fetcher_name, mode=mode)
                continue

            if not content or not content.strip():
                log_fail("网页抓取", "抓取内容为空", url=url, fetcher=fetcher_name, mode=mode)
                continue

            # HTML 类型需要清洗 + 转换
            if content_type == "html":
                try:
                    clean_content = extract_main_content(content)
                    result = convert_to_markdown(clean_content)

                    if len(result.strip()) < self._min_content_length:
                        log_fail("网页抓取", f"清洗后内容过短（{len(result.strip())} 字符），触发降级", url=url, fetcher=fetcher_name, mode=mode)
                        continue

                    log_ok("网页抓取", url=url, fetcher=fetcher_name, mode=mode)
                    return result
                    
                except Exception as e:
                    log_fail("HTML 清洗", e, url=url, fetcher=fetcher_name, fallback="返回原文")
                    return content.strip()

            # Markdown 类型直接返回
            log_ok("网页抓取", url=url, fetcher=fetcher_name, mode=mode, content_type=content_type)
            return content.strip()

        log_fail("网页抓取", "所有抓取器均失败", url=url, mode=mode)
        return None
