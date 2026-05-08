import httpx
from typing import Optional, Set, Tuple
from urllib.parse import urlparse

from common.logger import log_ok, log_fail, log_error


_SUPPORTED_DOC_MIME_TYPES: Set[str] = {
    "application/pdf",
    "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.ms-excel",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.ms-powerpoint",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
}

_TEXT_FRIENDLY_MIME_TYPES: Set[str] = {
    "application/json",
    "application/xml",
    "application/javascript",
    "application/x-javascript",
}

_SUPPORTED_DOC_EXTENSIONS: Tuple[str, ...] = (
    ".pdf",
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
    ".ppt",
    ".pptx",
)

_TEXT_FRIENDLY_EXTENSIONS: Tuple[str, ...] = (
    ".txt",
    ".md",
    ".json",
    ".xml",
    ".csv",
)

_MAX_RESPONSE_BYTES = 50 * 1024 * 1024
_READ_CHUNK_SIZE = 64 * 1024


class StaticFetcher:
    """轻量级静态 HTTP 抓取器"""

    def __init__(
        self,
        timeout: float = 10.0,
        max_retries: int = 3,
        max_response_bytes: int = _MAX_RESPONSE_BYTES,
    ):
        self._timeout = timeout
        self._max_retries = max_retries
        self._max_response_bytes = max_response_bytes
        self._headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        }

    async def fetch(self, url: str) -> Optional[str | bytes]:
        try:
            transport = httpx.AsyncHTTPTransport(retries=self._max_retries)

            async with httpx.AsyncClient(
                timeout=self._timeout,
                headers=self._headers,
                transport=transport,
                follow_redirects=True,
            ) as client:
                async with client.stream("GET", url) as response:
                    response.raise_for_status()

                    media_type = self._get_media_type(response)
                    path = urlparse(url).path.lower()

                    if not self._may_read_body(media_type, path):
                        log_fail(
                            "静态抓取",
                            f"不支持的 Content-Type: {media_type or 'unknown'}",
                            url=url,
                        )
                        return None

                    content = await self._read_limited(response, url)
                    if content is None:
                        return None

                    return self._route_response(
                        media_type=media_type,
                        path=path,
                        url=url,
                        content=content,
                    )

        except httpx.TimeoutException:
            log_fail("静态抓取", f"请求超时 {self._timeout}s", url=url)
            return None

        except httpx.ConnectError:
            log_fail("静态抓取", "连接失败", url=url)
            return None

        except httpx.HTTPStatusError as e:
            log_fail("静态抓取", f"HTTP {e.response.status_code}", url=url)
            return None

        except httpx.TooManyRedirects:
            log_fail("静态抓取", "重定向次数过多", url=url)
            return None

        except httpx.RequestError as e:
            log_fail("静态抓取", f"请求异常: {e.__class__.__name__}", url=url)
            return None

        except Exception as e:
            log_error("静态抓取", e, url=url)
            return None

    def _get_media_type(self, response: httpx.Response) -> str:
        return response.headers.get("content-type", "").lower().split(";")[0].strip()

    def _may_read_body(self, media_type: str, path: str) -> bool:
        if not media_type:
            return True

        if media_type == "application/octet-stream":
            return path.endswith(_SUPPORTED_DOC_EXTENSIONS) or path.endswith(_TEXT_FRIENDLY_EXTENSIONS)

        if self._is_text_like(media_type, path):
            return True

        if self._is_document_like(media_type, path):
            return True

        return False

    async def _read_limited(self, response: httpx.Response, url: str) -> Optional[bytes]:
        content_length = response.headers.get("content-length")

        if content_length:
            try:
                expected_size = int(content_length)
            except ValueError:
                expected_size = 0

            if expected_size > self._max_response_bytes:
                log_fail(
                    "静态抓取",
                    f"响应体过大({expected_size}字节)，上限{self._max_response_bytes}字节",
                    url=url,
                )
                return None

        chunks = []
        total_size = 0

        async for chunk in response.aiter_bytes(chunk_size=_READ_CHUNK_SIZE):
            total_size += len(chunk)

            if total_size > self._max_response_bytes:
                log_fail(
                    "静态抓取",
                    f"响应体超过上限({total_size}字节)，上限{self._max_response_bytes}字节",
                    url=url,
                )
                return None

            chunks.append(chunk)

        return b"".join(chunks)

    def _route_response(
        self,
        *,
        media_type: str,
        path: str,
        url: str,
        content: bytes,
    ) -> Optional[str | bytes]:
        if self._is_text_response(media_type, path, content):
            text = self._decode_text(response_content=content).strip()

            if not text:
                log_fail("静态抓取", "文本响应为空", url=url)
                return None

            log_ok("静态抓取", content_type=media_type or "unknown", size=len(content), url=url)
            return text

        if self._is_document_response(media_type, path):
            log_ok("静态抓取", content_type=media_type or "unknown", size=len(content), url=url)
            return content

        log_fail("静态抓取", f"不支持的 Content-Type: {media_type or 'unknown'}", url=url)
        return None

    def _is_text_like(self, media_type: str, path: str) -> bool:
        if media_type.startswith("text/"):
            return True

        if media_type in _TEXT_FRIENDLY_MIME_TYPES:
            return True

        if media_type.endswith("+json") or media_type.endswith("+xml"):
            return True

        if path.endswith(_TEXT_FRIENDLY_EXTENSIONS):
            return True

        return False

    def _is_document_like(self, media_type: str, path: str) -> bool:
        return media_type in _SUPPORTED_DOC_MIME_TYPES or path.endswith(_SUPPORTED_DOC_EXTENSIONS)

    def _is_text_response(self, media_type: str, path: str, content: bytes) -> bool:
        if self._is_text_like(media_type, path):
            return True

        head = content[:512].lstrip().lower()
        if head.startswith(b"<!doctype html") or head.startswith(b"<html") or b"<html" in head[:128]:
            return True

        return False

    def _is_document_response(self, media_type: str, path: str) -> bool:
        return self._is_document_like(media_type, path)

    def _decode_text(self, response_content: bytes) -> str:
        return response_content.decode("utf-8", errors="replace")
