from __future__ import annotations

import re

from scrapling.fetchers import StealthyFetcher

from common.logger import warn
from .base import RawFetchOutput
from ..errors import WebFetchHttpError, WebFetchNetworkError, WebFetchUnsupportedUrlError
from ..utils import decode_bytes

_URL_SCHEME_RE = re.compile(r"^https?://", re.IGNORECASE)

_DEFAULT_TIMEOUT_MS = 30_000


class ScraplingFetcher:
    """Scrapling StealthyFetcher 动态抓取器。只处理 HTML（非 HTML 由 httpx 拦截）。"""

    __slots__ = ("_timeout_ms", "_max_response_bytes")

    def __init__(
        self,
        *,
        timeout_ms: int = _DEFAULT_TIMEOUT_MS,
        max_response_bytes: int = 52_428_800,
    ) -> None:
        self._timeout_ms = timeout_ms
        self._max_response_bytes = max_response_bytes

    @property
    def name(self) -> str:
        return "scrapling"

    async def fetch(self, url: str) -> RawFetchOutput:
        if not _URL_SCHEME_RE.match(url.strip()):
            raise WebFetchUnsupportedUrlError(
                url=url,
                reason="unsupported url scheme, only http/https allowed",
            )

        try:
            response = await StealthyFetcher.async_fetch(
                url,
                headless=True,
                network_idle=True,
                timeout=self._timeout_ms,
            )
        except Exception as exc:
            raise WebFetchNetworkError(
                url=url,
                reason=f"scrapling fetch failed: {exc}",
            ) from exc

        # Response 字段：status, headers, url, body, encoding, history
        status: int = response.status
        if status >= 400:
            raise WebFetchHttpError(url=url, reason=f"http {status}")

        raw_bytes: bytes = response.body or b""
        if not raw_bytes:
            raise WebFetchNetworkError(url=url, reason="empty response body")

        if len(raw_bytes) > self._max_response_bytes:
            warn(
                "web_fetch scrapling response exceeded max bytes",
                url=url,
                max_bytes=self._max_response_bytes,
            )
            raise WebFetchNetworkError(
                url=url,
                reason=f"response exceeded max bytes {self._max_response_bytes}",
            )

        headers: dict[str, str] = dict(response.headers)
        content_type = headers.get("content-type")
        final_url: str = response.url
        encoding: str = response.encoding

        raw_html = decode_bytes(raw_bytes, encoding)

        return RawFetchOutput(
            source_url=url,
            fetcher=self.name,
            final_url=final_url,
            status_code=status,
            content_type=content_type,
            headers=headers,
            raw_html=raw_html,
        )