from typing import Optional

from steel import AsyncSteel

from common.logger import log_fail


class SteelFetcher:
    """通过 Steel API 的 scrape 接口获取页面内容"""

    def __init__(self, steel_base_url: str, timeout: float = 60.0):
        self._client = AsyncSteel(
            base_url=steel_base_url,
            timeout=timeout,
        )

    async def fetch(self, url: str) -> Optional[str]:
        """利用 Steel 抓取指定 URL 的页面内容"""
        try:
            response = await self._client.scrape(url=url, format=["markdown"])
            return response.markdown or response.content or response.text
        except Exception as e:
            log_fail("Steel 浏览器抓取", e, url=url)
            return None
