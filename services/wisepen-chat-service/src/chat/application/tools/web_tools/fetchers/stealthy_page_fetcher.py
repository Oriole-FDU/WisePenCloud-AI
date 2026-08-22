from __future__ import annotations

from typing import Any

from .base import RawFetchOutput
from .static_page_fetcher import (
    build_raw_fetch_output,
    UrlFetchError,
    UrlFetchNetworkError,
    UrlFetchHttpError,
    UrlFetchUnsupportedUrlError
)


class StealthyPageFetcher:
    """调用共享 Scrapling 浏览器 session；URL 已在工具边界完成校验。"""

    def __init__(self, *, session: Any, max_response_bytes: int = 52_428_800) -> None:
        self._session = session
        self._max_response_bytes = max_response_bytes

    async def fetch(self, url: str) -> RawFetchOutput:
        try:
            response = await self._session.fetch(url)
            return build_raw_fetch_output(
                response,
                source_url=url,
                max_response_bytes=self._max_response_bytes,
            )
        except (
            UrlFetchError,
            UrlFetchHttpError,
            UrlFetchNetworkError,
            UrlFetchUnsupportedUrlError,
        ):
            raise
        except Exception as exc:
            raise UrlFetchNetworkError(
                url=url,
                reason=f"stealthy page fetch failed: {exc}",
            ) from exc
