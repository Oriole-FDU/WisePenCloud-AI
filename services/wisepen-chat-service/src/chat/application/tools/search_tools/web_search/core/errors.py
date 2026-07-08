from __future__ import annotations

from chat.application.tools.search_tools.web_search.providers.models import SearchProviderName


class WebSearchError(RuntimeError):
    """Web search 基础异常。"""


class WebSearchCustomError(WebSearchError):
    """custom 搜索源异常。"""

    def __init__(self, *, provider: SearchProviderName | None, reason: str) -> None:
        super().__init__(f"{provider}: {reason}")
        self.provider = provider  # 出错的自定义搜索源
        self.reason = reason  # 可给 tool 门面或 API 层展示/记录的原因

    def __str__(self) -> str:
        return f"{self.provider}: {self.reason}"


class WebSearchProviderRuntimeError(WebSearchError):
    """已定位到 provider 的运行时异常。"""

    def __init__(self, *, provider: SearchProviderName | None, reason: str) -> None:
        super().__init__(f"{provider}: {reason}")
        self.provider = provider  # 出错的搜索源
        self.reason = reason  # 可给 tool 门面或 API 层展示/记录的原因


class WebSearchCustomApiKeyMissing(WebSearchCustomError):
    """custom 搜索源缺少 api key。"""


class WebSearchCustomApiKeyInvalid(WebSearchCustomError):
    """custom api key 失效、过期或额度耗尽。"""


class WebSearchEmptyResult(WebSearchProviderRuntimeError):
    """搜索源成功响应但返回为空。"""


class WebSearchNetworkError(WebSearchProviderRuntimeError):
    """搜索源网络波动或连接失败。"""


class WebSearchInternalError(WebSearchProviderRuntimeError):
    """内部代码错误或未预期异常。"""
