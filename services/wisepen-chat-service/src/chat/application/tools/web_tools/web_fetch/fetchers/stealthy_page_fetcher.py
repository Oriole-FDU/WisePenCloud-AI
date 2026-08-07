from __future__ import annotations

from typing import Any

from chat.application.utils.url_security import (
    UrlSecurityError,
    validate_public_http_url_async,
)

from ..core.errors import (
    UrlFetchHttpError,
    UrlFetchNetworkError,
    UrlFetchUnsupportedUrlError,
)
from ..core.models import RawFetchOutput
from .base import build_raw_fetch_output

class StealthyPageFetcher:
    """浏览器 HTML 页面抓取器。session 生命周期和抓取策略由容器管理。"""

    __slots__ = ("_max_response_bytes", "_session")

    def __init__(
        self,
        *,
        session: Any,
        max_response_bytes: int = 52_428_800,
    ) -> None:
        self._session = session
        self._max_response_bytes = max_response_bytes

    async def fetch(self, url: str) -> RawFetchOutput:
        try:
            url = await validate_public_http_url_async(url.strip())
            response = await self._session.fetch(url)
            return build_raw_fetch_output(
                response,
                source_url=url,
                max_response_bytes=self._max_response_bytes,
            )
        except (
            UrlSecurityError,
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
