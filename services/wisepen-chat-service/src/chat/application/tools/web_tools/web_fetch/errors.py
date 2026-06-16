from __future__ import annotations


class WebFetchError(RuntimeError):
    """Web fetch 基础异常。

    所有 web_fetch 内部异常的基类。工具门面层负责把 WebFetchError
    映射为 ToolExecutionError，服务层内部不直接抛给 LLM。
    """

    def __init__(self, *, url: str, reason: str) -> None:
        super().__init__(f"{url}: {reason}")
        self.url = url
        self.reason = reason

    def __str__(self) -> str:
        return f"{self.url}: {self.reason}"


class WebFetchNetworkError(WebFetchError):
    """网络层失败（超时、连接拒绝、DNS 解析失败等）。"""


class WebFetchHttpError(WebFetchError):
    """HTTP 层失败（4xx/5xx）。"""


class WebFetchEmptyContentError(WebFetchError):
    """抓取成功但正文为空或清洗后为空。"""


class WebFetchUnsupportedUrlError(WebFetchError):
    """不支持的 URL 协议或被安全策略拒绝的 URL。"""
