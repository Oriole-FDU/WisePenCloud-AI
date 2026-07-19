from __future__ import annotations

from chat.application.tools.search_tools.web_search.services.providers.core.models import (
    SearchProviderName,
)


class WebSearchError(RuntimeError):
    """Web Search 的基础异常。"""


class WebSearchCustomError(WebSearchError):
    """用户配置的搜索源发生异常。"""

    def __init__(self, *, provider: SearchProviderName | None, reason: str) -> None:
        super().__init__(f"{provider}: {reason}")
        self.provider = provider
        self.reason = reason


class WebSearchProviderError(WebSearchError):
    """已定位到搜索源的运行时异常。"""

    def __init__(self, *, provider: SearchProviderName | None, reason: str) -> None:
        super().__init__(f"{provider}: {reason}")
        self.provider = provider
        self.reason = reason


class WebSearchCustomApiKeyMissing(WebSearchCustomError):
    """用户配置的搜索源缺少 API key。"""


class WebSearchCustomApiKeyInvalid(WebSearchCustomError):
    """用户配置的 API key 无效、过期或额度不足。"""


class WebSearchEmptyResult(WebSearchProviderError):
    """搜索源成功响应但没有结果。"""


class WebSearchNetworkError(WebSearchProviderError):
    """搜索源网络不可用。"""


class WebSearchInternalError(WebSearchProviderError):
    """搜索源出现未预期的内部错误。"""
