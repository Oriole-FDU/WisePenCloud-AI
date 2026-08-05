from __future__ import annotations

from typing import Protocol

from charset_normalizer import from_bytes as detect_encoding
from scrapling.engines.toolbelt.custom import Response

from ..core.errors import UrlFetchHttpError, UrlFetchNetworkError, UrlFetchUnsupportedUrlError
from ..core.models import RawFetchOutput

_HTML_CONTENT_TYPES = {"text/html", "application/xhtml+xml"}
_HTML_MARKERS = (b"<!doctype html", b"<html", b"<head", b"<body")
_PDF_MAGIC = b"%PDF-"


class WebFetcher(Protocol):
    async def fetch(self, url: str) -> RawFetchOutput:
        ...


def build_raw_fetch_output(
    response: Response,
    *,
    source_url: str,
    max_response_bytes: int,
) -> RawFetchOutput:
    """将抓取响应分类为 HTML 或 PDF 原始结果。"""
    status = response.status

    if (
        300 <= status < 400
        or response.history
        or response.url.strip() != source_url
    ):
        raise UrlFetchUnsupportedUrlError(
            url=source_url,
            reason="redirect_not_allowed"
        )

    if status >= 400:
        raise UrlFetchHttpError(
            url=source_url,
            reason=f"http {status}"
        )

    body = response.body
    if not body:
        raise UrlFetchNetworkError(
            url=source_url,
            reason="empty response body"
        )

    if len(body) > max_response_bytes:
        raise UrlFetchNetworkError(
            url=source_url,
            reason=f"response exceeded max bytes {max_response_bytes}"
        )

    content_type = response.headers.get("content-type") or ""
    media_type = content_type.partition(";")[0].strip().lower()
    headers = {str(k).lower(): str(v) for k, v in response.headers.items()}

    # Content-Type 可能缺失或不可信，因此同时检查文件签名。
    if media_type == "application/pdf" or body.lstrip().startswith(_PDF_MAGIC):
        return RawFetchOutput(
            source_url=source_url,
            headers=headers,
            pdf_bytes=body
        )

    # HTML 同样兼容缺失 Content-Type 的情况。
    if (
        media_type not in _HTML_CONTENT_TYPES
        and not body.lstrip()[:512].lower().startswith(_HTML_MARKERS)
    ):
        raise UrlFetchUnsupportedUrlError(
            url=source_url,
            reason="unsupported_content_type"
        )

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

    isolation = ["utf-8", "gbk", "big5", "shift_jis", "euc_kr"]
    detected = detect_encoding(raw, cp_isolation=isolation).best()

    return str(detected) if detected else raw.decode("utf-8", errors="replace")
