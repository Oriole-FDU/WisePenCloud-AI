import httpx
from typing import Optional

from common.logger import log_fail, log_error


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

    async def fetch(self, url: str) -> Optional[str]:
        """发起静态 HTTP 请求获取页面 HTMLL """
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
                return response.text

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
