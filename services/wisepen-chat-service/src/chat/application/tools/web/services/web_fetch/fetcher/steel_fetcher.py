import asyncio
from dataclasses import dataclass, field
from typing import List, Optional

import steel
from steel import AsyncSteel

from chat.application.tools.web.services.web_fetch.enums import FetcherName
from chat.application.tools.web.services.web_fetch.fetcher.base import BaseFetcher
from chat.application.tools.web.services.web_fetch.fetcher.content_processor import ContentProcessor
from chat.application.tools.web.services.web_fetch.models import FetchedPage
from chat.application.tools.web.utils.domains import extract_domain
from chat.application.tools.web.utils.markdown import extract_markdown_title
from common.logger import log_fail


@dataclass(frozen=True, slots=True)
class SteelFetcherConfig:
    """Steel 浏览器抓取器的配置参数，包含超时、延迟及输出格式。"""
    base_url: str
    timeout: float = 30.0
    delay_ms: float = 2000.0
    formats: List[str] = field(
        default_factory=lambda: ["markdown", "cleaned_html", "html"]
    )


class SteelFetcher(BaseFetcher):
    """通过 Steel API 的 scrape 接口获取页面 Markdown 内容。"""

    @property
    def name(self) -> FetcherName:
        """返回当前抓取器的唯一标识名称。"""
        return FetcherName.STEEL

    def __init__(
        self,
        config: SteelFetcherConfig,
        client: AsyncSteel,
        processor: ContentProcessor,
        *,
        concurrency: int = 3,
    ):
        """注入 Steel 配置、API 客户端、内容处理器及并发控制信号量。"""
        self._config = config
        self._client = client
        self._processor = processor
        self._semaphore = asyncio.Semaphore(concurrency)

    async def fetch(self, url: str) -> Optional[FetchedPage]:
        """调用 Steel API 抓取页面，并按格式优先级尝试获取有效 Markdown。"""
        async with self._semaphore:
            try:
                params = {
                    "url": url,
                    "format": self._config.formats,
                    "timeout": self._config.timeout,
                }

                if self._config.delay_ms > 0:
                    params["delay"] = self._config.delay_ms

                response = await self._client.scrape(**params)

                # 检查 HTTP 层面错误
                metadata = response.metadata
                status_code = metadata.status_code if metadata is not None else None
                if status_code is not None and status_code >= 400:
                    return None

                content = response.content
                if content is None:
                    return None

                markdown: Optional[str] = None

                # 按配置格式优先级逐个尝试，获取第一个能通过内容清洗的结果
                for output_format in self._config.formats:
                    if output_format == "markdown":
                        candidate = content.markdown
                    elif output_format == "cleaned_html":
                        candidate = content.cleaned_html
                    else:
                        candidate = content.html

                    if not candidate:
                        continue

                    processed = await asyncio.to_thread(
                        self._processor.process,
                        candidate,
                    )
                    if processed:
                        markdown = processed
                        break

                if markdown is None:
                    return None

                final_url = url
                title = extract_markdown_title(markdown)

                if metadata is not None:
                    if metadata.final_url:
                        final_url = metadata.final_url
                    if metadata.title:
                        title = metadata.title.strip()

                return FetchedPage(
                    markdown=markdown,
                    links=[],
                    title=title,
                    final_url=final_url,
                    domain=extract_domain(final_url),
                    status_code=status_code,
                )

            except steel.APIError as e:
                log_fail("SteelFetcher", e, url=url)
                return None
            except Exception as e:
                log_fail("SteelFetcher", e, url=url)
                return None