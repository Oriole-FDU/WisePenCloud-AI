import asyncio
import re
from pathlib import PurePosixPath
from typing import List, Optional, Union
from urllib.parse import unquote, urljoin, urlparse

import httpx

from chat.application.security.url_security import validate_public_http_url
from chat.application.tools.web.services.common.file_type_detection.detector import FileTypeDetector
from chat.application.tools.web.services.common.file_type_detection.enums import ContentKind
from chat.application.tools.web.services.common.file_type_detection.models import ContentDetection
from chat.application.tools.web.services.web_fetch.enums import FetcherName
from chat.application.tools.web.services.web_fetch.errors import UnsupportedMediaError
from chat.application.tools.web.services.web_fetch.fetcher.base import BaseFetcher
from chat.application.tools.web.services.web_fetch.fetcher.content_processor import ContentProcessor
from chat.application.tools.web.services.web_fetch.models import (
    FetchedDocument,
    FetchedPage,
    FetchedRedirect,
)
from chat.application.tools.web.utils.domains import extract_domain
from chat.application.tools.web.utils.filename import (
    drop_dangerous_inner_suffix,
    sanitize_download_filename,
)
from chat.application.tools.web.utils.markdown import extract_markdown_title
from common.logger import log_event

FetchedContent = Union[FetchedDocument, FetchedPage, FetchedRedirect]

_CONTENT_DISPOSITION_FILENAME_STAR_RE = re.compile(
    r"filename\*\s*=\s*([^;]+)",
    re.IGNORECASE,
)
_CONTENT_DISPOSITION_FILENAME_RE = re.compile(
    r"filename\s*=\s*(?P<filename>\"[^\"]+\"|'[^']+'|[^;]+)",
    re.IGNORECASE,
)
_META_CHARSET_RE = re.compile(
    rb"<meta[^>]+charset\s*=\s*['\"]?([^'\"\s/>;]+)",
    re.IGNORECASE,
)
_CONTENT_TYPE_CHARSET_RE = re.compile(
    r"charset\s*=\s*['\"]?([^;'\"]+)",
    re.IGNORECASE,
)

_TEXT_KINDS = {ContentKind.HTML, ContentKind.JSON, ContentKind.XML, ContentKind.TEXT}


class StaticFetcher(BaseFetcher):
    """轻量级静态 HTTP 抓取器，通过 httpx 发送 HTTP 请求获取网页内容。"""

    @property
    def name(self) -> FetcherName:
        """返回当前抓取器的唯一标识名称。"""
        return FetcherName.STATIC

    def __init__(
        self,
        client: httpx.AsyncClient,
        filetype_detector: FileTypeDetector,
        processor: ContentProcessor,
        max_response_bytes: int = 50 * 1024 * 1024,
        max_redirects: int = 5,
    ):
        """注入 HTTP 客户端、文件类型检测器和内容处理器。"""
        self._client = client
        self._max_response_bytes = max_response_bytes
        self._max_redirects = max_redirects
        self._filetype_detector = filetype_detector
        self._processor = processor

    async def fetch(self, url: str) -> Optional[FetchedContent]:
        """发送 HTTP 请求，手动处理重定向链，对响应进行类型检测和内容清洗。"""
        redirect_count = 0
        current_url = url

        try:
            # 重定向手动跟随循环
            while True:
                async with self._client.stream("GET", current_url) as response:

                    # 处理 3xx 重定向
                    if response.status_code >= 300 and response.status_code < 400:
                        if redirect_count >= self._max_redirects:
                            return None

                        location = response.headers.get("location")
                        if not location:
                            return None

                        next_url = urljoin(str(response.url), location)
                        next_url = await asyncio.to_thread(
                            validate_public_http_url, next_url
                        )

                        # 同站跳转继续跟随，跨站跳转返回给上游决策
                        if not extract_domain(current_url) == extract_domain(next_url):
                            return FetchedRedirect(
                                url=current_url,
                                redirect_url=next_url,
                                status_code=response.status_code,
                            )

                        current_url = next_url
                        redirect_count += 1
                        continue

                    # 非重定向响应处理
                    response.raise_for_status()

                    content_type_header = response.headers.get("content-type", "")
                    content_disposition = response.headers.get(
                        "content-disposition", ""
                    )

                    content = await _read_limited(
                        response,
                        max_response_bytes=self._max_response_bytes,
                    )
                    if content is None:
                        return None

                    # 根据真实文件类型分流处理
                    result = await _build_web_fetch_result(
                        url=current_url,
                        content=content,
                        content_type_header=content_type_header,
                        content_disposition=content_disposition,
                        detector=self._filetype_detector,
                    )

                    if result is None:
                        return None

                    # 文档直接返回
                    if isinstance(result, FetchedDocument):
                        return result

                    # 文本进入清洗流程
                    processed = await asyncio.to_thread(self._processor.process, result)
                    if not processed:
                        return None

                    final_url = str(response.url)
                    return FetchedPage(
                        markdown=processed,
                        links=[],
                        title=extract_markdown_title(processed),
                        final_url=final_url,
                        domain=extract_domain(final_url),
                        status_code=response.status_code,
                    )

        except (
            httpx.TimeoutException,
            httpx.ConnectError,
            httpx.HTTPStatusError,
            httpx.RequestError,
        ):
            return None


async def _read_limited(
    response: httpx.Response,
    *,
    max_response_bytes: int,
) -> Optional[bytes]:
    """流式读取 HTTP 响应体，限制最大字节数防止 OOM。"""
    content_length = response.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > max_response_bytes:
                return None
        except ValueError:
            pass

    # 动态流式读取，逐块累积并检查上限
    chunks: List[bytes] = []
    total_size = 0

    async for chunk in response.aiter_bytes(chunk_size=64 * 1024):
        total_size += len(chunk)

        if total_size > max_response_bytes:
            return None

        chunks.append(chunk)

    return b"".join(chunks)


async def _build_web_fetch_result(
    *,
    url: str,
    content: bytes,
    content_type_header: str,
    content_disposition: str,
    detector: FileTypeDetector,
) -> Optional[Union[str, FetchedDocument]]:
    """根据文件类型检测结果分发处理：文档直接返回、文本解码、媒体资源报告异常。"""
    detection = await detector.detect_bytes(content)

    if detection.kind == ContentKind.DOCUMENT:
        return FetchedDocument(
            url=url,
            media_type=detection.mime_type,
            filename=_build_document_filename(
                url=url,
                content_disposition=content_disposition,
                detection=detection,
            ),
            content=content,
        )

    if detection.kind in _TEXT_KINDS:
        text = _decode_text_response(
            content,
            content_type_header=content_type_header,
        ).strip()

        if not text:
            return None

        return text

    if detection.kind in {ContentKind.IMAGE, ContentKind.UNSUPPORTED_MEDIA}:
        log_event(
            "web_fetch_unsupported_media_detected",
            content_type=detection.mime_type,
            action="stop_static_fetch",
            url=url,
        )
        raise UnsupportedMediaError(url=url, media_type=detection.mime_type)

    return None


def _build_document_filename(
    *,
    url: str,
    content_disposition: str,
    detection: ContentDetection,
) -> str:
    """从 Content-Disposition 头或 URL 路径中提取并构造安全的文档文件名。"""
    base: Optional[str] = None

    # 优先从 filename* (RFC 5987) 提取
    if match := _CONTENT_DISPOSITION_FILENAME_STAR_RE.search(content_disposition):
        raw = match.group(1).strip().strip("\"'")

        try:
            charset, _, encoded = raw.split("'", 2)
        except ValueError:
            pass
        else:
            try:
                base = unquote(encoded, encoding=charset or "utf-8").strip() or None
            except LookupError:
                base = (
                    unquote(encoded, encoding="utf-8", errors="replace").strip()
                    or None
                )

    # 回退到 filename 提取
    if not base:
        if match := _CONTENT_DISPOSITION_FILENAME_RE.search(content_disposition):
            base = match.group("filename").strip().strip("\"'").strip() or None

    # 最后从 URL 路径中提取文件名
    if not base:
        base = PurePosixPath(unquote(urlparse(url).path).replace("\\", "/")).name

    stem = PurePosixPath(sanitize_download_filename(base or "download")).stem
    return drop_dangerous_inner_suffix(f"{stem or 'download'}{detection.extension or ''}")


def _decode_text_response(content: bytes, *, content_type_header: str) -> str:
    """根据 Content-Type 或 HTML meta 标签声明的编码解码文本响应。"""
    def decode_with(encoding: str) -> Optional[str]:
        """尝试使用指定编码解码字节数据。"""
        try:
            return content.decode(encoding, errors="replace")
        except LookupError:
            return None

    # 优先使用 Content-Type 头中的 charset
    if match := _CONTENT_TYPE_CHARSET_RE.search(content_type_header):
        if decoded := decode_with(match.group(1).strip()):
            return decoded

    # 回退到 HTML meta 标签中的 charset
    if match := _META_CHARSET_RE.search(content[:4096]):
        encoding = match.group(1).decode("ascii", errors="replace").strip()

        if decoded := decode_with(encoding):
            return decoded

    # 默认使用 UTF-8
    return content.decode("utf-8", errors="replace")