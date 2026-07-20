from __future__ import annotations

from typing import Any

from ..services.models import SearchProviderName
from .base import BaseWebSearchTool


class PlatformSearchTool(BaseWebSearchTool):
    def __init__(self, **kwargs: Any) -> None:
        super().__init__(
            tool_name="platform_search",
            provider=None,
            **kwargs,
        )


class ExaSearchTool(BaseWebSearchTool):
    def __init__(self, **kwargs: Any) -> None:
        super().__init__(
            tool_name="exa_search",
            provider=SearchProviderName.EXA,
            **kwargs,
        )


class TavilySearchTool(BaseWebSearchTool):
    def __init__(self, **kwargs: Any) -> None:
        super().__init__(
            tool_name="tavily_search",
            provider=SearchProviderName.TAVILY,
            **kwargs,
        )


class AnySearchSearchTool(BaseWebSearchTool):
    def __init__(self, **kwargs: Any) -> None:
        super().__init__(
            tool_name="anysearch_search",
            provider=SearchProviderName.ANYSEARCH,
            **kwargs,
        )


class BaiduQianfanSearchTool(BaseWebSearchTool):
    def __init__(self, **kwargs: Any) -> None:
        super().__init__(
            tool_name="baidu_qianfan_search",
            provider=SearchProviderName.BAIDU_QIANFAN,
            **kwargs,
        )
