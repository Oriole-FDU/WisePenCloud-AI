from __future__ import annotations

from dataclasses import dataclass

import httpx

from .models import SearchProviderName
from .providers import (
    AnySearchSearcher,
    BaiduQianfanSearcher,
    ExaSearcher,
    FirecrawlSearcher,
    PlatformDefaultSearcher,
    TavilySearcher,
    TinyFishSearcher,
)
from .providers.base import (
    ProviderSearcher,
    SearchProviderConfig,
)


@dataclass(frozen=True, slots=True)
class SearchSourceFactory:
    """只负责将工具配置路由为运行时搜索源。"""

    http_client: httpx.AsyncClient
    platform_default_searcher: PlatformDefaultSearcher
    exa_base_url: str
    tavily_base_url: str
    anysearch_base_url: str
    baidu_qianfan_base_url: str
    tinyfish_base_url: str
    firecrawl_base_url: str

    def build(
        self,
        *,
        provider: SearchProviderName | None,
        api_key: str | None,
    ) -> ProviderSearcher:
        if provider is None:
            return self.platform_default_searcher

        return self._build_custom_searcher(
            provider=provider,
            api_key=api_key,
        )

    def _build_custom_searcher(
        self,
        *,
        provider: SearchProviderName,
        api_key: str,
    ) -> ProviderSearcher:
        config = SearchProviderConfig(
            base_url=self._base_url(provider),
            api_key=api_key,
        )

        if provider == SearchProviderName.EXA:
            return ExaSearcher(
                http_client=self.http_client,
                config=config,
            )

        if provider == SearchProviderName.TAVILY:
            return TavilySearcher(
                http_client=self.http_client,
                config=config,
            )

        if provider == SearchProviderName.ANYSEARCH:
            return AnySearchSearcher(
                http_client=self.http_client,
                config=config,
            )

        if provider == SearchProviderName.BAIDU_QIANFAN:
            return BaiduQianfanSearcher(
                http_client=self.http_client,
                config=config,
            )

        if provider == SearchProviderName.TINYFISH:
            return TinyFishSearcher(
                http_client=self.http_client,
                config=config,
            )

        if provider == SearchProviderName.FIRECRAWL:
            return FirecrawlSearcher(
                http_client=self.http_client,
                config=config,
            )

        raise ValueError(f"不支持的搜索源: {provider}")

    def _base_url(
        self,
        provider: SearchProviderName,
    ) -> str:
        if provider == SearchProviderName.EXA:
            return self.exa_base_url

        if provider == SearchProviderName.TAVILY:
            return self.tavily_base_url

        if provider == SearchProviderName.ANYSEARCH:
            return self.anysearch_base_url

        if provider == SearchProviderName.BAIDU_QIANFAN:
            return self.baidu_qianfan_base_url

        if provider == SearchProviderName.TINYFISH:
            return self.tinyfish_base_url

        if provider == SearchProviderName.FIRECRAWL:
            return self.firecrawl_base_url

        raise ValueError(f"不支持的搜索源: {provider}")
