from typing import List


class WebSearchError(Exception):
    """全联网检索业务的刚性异常基类"""

    pass


class SearchProviderError(WebSearchError):
    """搜索提供商返回 HTTP 错误时的异常。"""

    def __init__(self, provider: str, status_code: int, reason: str = "") -> None:
        self.provider = provider
        self.status_code = status_code
        self.reason = reason
        detail = f"{provider} search provider failed with status {status_code}"
        if reason:
            detail = f"{detail}: {reason}"
        super().__init__(detail)


class SearchTimeoutError(WebSearchError):
    """搜索请求超时引发的异常。"""

    def __init__(self, provider: str, queries: List[str], timeout: float) -> None:
        self.provider = provider
        self.queries = queries
        self.timeout = timeout
        super().__init__(
            f"{provider} search timed out after {timeout} seconds."
        )


class SearchRateLimitError(WebSearchError):
    """搜索请求被限频（HTTP 429）时引发的异常。"""

    def __init__(self, provider: str, retry_after: int = 0) -> None:
        self.provider = provider
        self.retry_after = retry_after
        super().__init__(f"{provider} search provider rate limited the request.")


class EmptySearchResultError(WebSearchError):
    """所有搜索渠道均返回空结果时引发的异常。"""

    def __init__(self, provider: str, queries: List[str]) -> None:
        self.provider = provider
        self.queries = queries
        super().__init__(f"{provider} returned no search results.")


class CustomSearchProviderUnavailableError(WebSearchError):
    """用户自定义搜索源不可用时引发的异常。"""

    def __init__(
        self,
        *,
        provider: str,
        message: str,
    ) -> None:
        self.provider = provider
        super().__init__(message)


class SearchProviderTransientError(WebSearchError):
    """搜索提供商返回临时性错误时的异常，上层可据此决定重试。"""

    def __init__(self, provider: str, queries: List[str], reason: str = "") -> None:
        self.provider = provider
        self.queries = queries
        self.reason = reason
        detail = f"{provider} search provider returned a transient error"
        if reason:
            detail = f"{detail}: {reason}"
        super().__init__(detail)
