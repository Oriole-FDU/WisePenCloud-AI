from __future__ import annotations

from typing import Any

from .base import (
    RawFetchOutput,
    UrlFetchError,
    UrlFetchNetworkError,
    build_raw_fetch_output,
)


class StaticPageFetcher:
    """调用共享 Scrapling 静态 session；URL 安全校验由工具入口负责。"""

    def __init__(self, *, session: Any, max_response_bytes: int = 52_428_800) -> None:
        self._session = session
        self._max_response_bytes = max_response_bytes

    async def fetch(self, url: str) -> RawFetchOutput:
        try:
            response = await self._session.get(url, follow_redirects=False)
            return build_raw_fetch_output(
                response,
                source_url=url,
                max_response_bytes=self._max_response_bytes,
            )
        except UrlFetchError:
            raise
        except Exception as exc:
            raise UrlFetchNetworkError(
                url=url,
                reason=f"static page fetch failed: {exc}",
            ) from exc


