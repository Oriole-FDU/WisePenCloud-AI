from __future__ import annotations

from typing import Any

from charset_normalizer import from_bytes as detect_encoding

from .base import RawFetchOutput

_HTML_CONTENT_TYPES = {"text/html", "application/xhtml+xml"}
_HTML_MARKERS = (b"<!doctype html", b"<html", b"<head", b"<body")
_PDF_MAGIC = b"%PDF-"


class UrlFetchError(RuntimeError):
    """一个 URL 的抓取失败，不应中断同批其他 URL。"""

    def __init__(self, *, url: str, reason: str) -> None:
        super().__init__(f"{url}: {reason}")
        self.url = url
        self.reason = reason


class UrlFetchNetworkError(UrlFetchError):
    """网络层或响应大小限制失败。"""


class UrlFetchHttpError(UrlFetchError):
    """HTTP 状态码失败。"""


class UrlFetchUnsupportedUrlError(UrlFetchError):
    """重定向、协议或内容类型不受支持。"""


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
                reason=f"static page fetch failed: {exc}",
            ) from exc


def build_raw_fetch_output(
        response: Any,
        *,
        source_url: str,
        max_response_bytes: int,
) -> RawFetchOutput:
    """拒绝重定向并依据签名将响应分类为 HTML、PDF 或不支持内容。"""
    status = int(response.status)
    if (
            300 <= status < 400
            or response.history
            or str(response.url).strip() != source_url
    ):
        raise UrlFetchUnsupportedUrlError(url=source_url, reason="redirect_not_allowed")
    if status >= 400:
        raise UrlFetchHttpError(url=source_url, reason=f"http {status}")

    body = response.body
    if not body:
        raise UrlFetchNetworkError(url=source_url, reason="empty response body")
    if len(body) > max_response_bytes:
        raise UrlFetchNetworkError(
            url=source_url,
            reason=f"response exceeded max bytes {max_response_bytes}",
        )

    headers = {str(key).lower(): str(value) for key, value in response.headers.items()}
    media_type = (headers.get("content-type") or "").partition(";")[0].strip().lower()
    if media_type == "application/pdf" or body.lstrip().startswith(_PDF_MAGIC):
        return RawFetchOutput(source_url=source_url, headers=headers, pdf_bytes=body)
    if media_type not in _HTML_CONTENT_TYPES and not body.lstrip()[:512].lower().startswith(
            _HTML_MARKERS
    ):
        raise UrlFetchUnsupportedUrlError(url=source_url, reason="unsupported_content_type")
    return RawFetchOutput(
        source_url=source_url,
        headers=headers,
        raw_html=_decode_body(body, response.encoding),
    )


def _decode_body(raw: bytes, declared_encoding: str | None) -> str:
    if declared_encoding:
        try:
            return raw.decode(declared_encoding, errors="replace")
        except LookupError:
            pass
    detected = detect_encoding(
        raw,
        cp_isolation=["utf-8", "gbk", "big5", "shift_jis", "euc_kr"],
    ).best()
    return str(detected) if detected else raw.decode("utf-8", errors="replace")
