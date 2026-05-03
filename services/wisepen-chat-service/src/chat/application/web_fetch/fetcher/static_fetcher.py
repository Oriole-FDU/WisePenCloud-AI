import httpx
from typing import Optional, Set

from common.logger import log_ok, log_fail, log_error

_SUPPORTED_DOC_MIME_TYPES: Set[str] = {  # 二进制文档 MIME 类型白名单，命中则返回 bytes
    "application/pdf",
    "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.ms-excel",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.ms-powerpoint",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
}

_TEXT_FRIENDLY_MIME_TYPES: Set[str] = {  # 非文本类但内容为纯文本的 MIME 类型，命中则返回 str
    "application/json",
    "application/xml",
    "application/javascript",
}


class StaticFetcher:
    """轻量级静态 HTTP 抓取器"""

    def __init__(self, timeout: float = 10.0, max_retries: int = 3):
        self._timeout = timeout
        self._max_retries = max_retries
        self._headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
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
                response = await client.get(url)
                response.raise_for_status()
                return self._route_response(response, url)

        except httpx.TimeoutException:
            log_fail("静态抓取", f"请求超时 {self._timeout}s", url=url)
            return None

        except httpx.ConnectError:
            log_fail("静态抓取", "连接失败", url=url)
            return None

        except httpx.HTTPStatusError as e:
            log_fail("静态抓取", f"HTTP {e.response.status_code}", url=url)
            return None

        except httpx.RequestError:
            log_fail("静态抓取", "请求异常", url=url)
            return None

        except Exception as e:
            log_error("静态抓取", e, url=url)
            return None

    def _route_response(self, response: httpx.Response, url: str) -> Optional[str | bytes]:
        media_type = response.headers.get("content-type", "").lower().split(";")[0].strip()

        if media_type.startswith("text/") or media_type in _TEXT_FRIENDLY_MIME_TYPES:
            return response.text

        if media_type in _SUPPORTED_DOC_MIME_TYPES:
            log_ok("静态抓取", content_type=media_type, url=url)
            return response.content

        log_fail("静态抓取", f"不支持的 Content-Type: {media_type}", url=url)
        return None
