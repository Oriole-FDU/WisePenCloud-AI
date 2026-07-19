class SearchProviderError(RuntimeError):
    """搜索源请求或响应解析失败。"""


class SearchProviderCredentialError(SearchProviderError):
    """搜索源凭证无效、过期或额度不足。"""


class SearchProviderNetworkError(SearchProviderError):
    """搜索源网络不可用。"""
