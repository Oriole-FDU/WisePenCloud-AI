from __future__ import annotations


class UrlFetchError(RuntimeError):
    """web_fetch 抓取基础异常。"""

    def __init__(self, *, url: str, reason: str) -> None:
        super().__init__(f"{url}: {reason}")
        self.url = url
        self.reason = reason

    def __str__(self) -> str:
        return f"{self.url}: {self.reason}"


class UrlFetchNetworkError(UrlFetchError):
    """网络层失败。"""


class UrlFetchHttpError(UrlFetchError):
    """HTTP 层失败。"""


class UrlFetchUnsupportedUrlError(UrlFetchError):
    """不支持的 URL 协议。"""
