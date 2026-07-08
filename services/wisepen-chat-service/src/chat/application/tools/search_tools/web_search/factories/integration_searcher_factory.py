from __future__ import annotations

from dataclasses import dataclass

import httpx

from chat.application.tools.search_tools.web_search.core.errors import WebSearchCustomApiKeyInvalid
from chat.application.tools.search_tools.web_search.providers.models import SearchProviderName
from chat.application.tools.search_tools.web_search.searchers import (
    AnySearchSearcher,
    BaiduQianfanSearcher,
    BaseProviderSearcher,
    ExaSearcher,
    SearchProviderConfig,
    TavilySearcher,
)


@dataclass(frozen=True, slots=True)
class IntegrationSearcherFactory:
    """构造可被 platform_member 和 custom 复用的第三方 provider adapter。"""

    http_client: httpx.AsyncClient
    exa_base_url: str
    tavily_base_url: str
    anysearch_base_url: str
    baidu_qianfan_base_url: str

    def build(
            self,
            *,
            provider: SearchProviderName,
            api_key: str,
            source_id: str,
    ) -> BaseProviderSearcher:
        config = SearchProviderConfig(
            base_url=self._base_url(provider),
            api_key=api_key,
            source_id=source_id,
        )
        if provider == SearchProviderName.EXA:
            return ExaSearcher(http_client=self.http_client, config=config)
        if provider == SearchProviderName.TAVILY:
            return TavilySearcher(http_client=self.http_client, config=config)
        if provider == SearchProviderName.ANYSEARCH:
            return AnySearchSearcher(http_client=self.http_client, config=config)
        if provider == SearchProviderName.BAIDU_QIANFAN:
            return BaiduQianfanSearcher(http_client=self.http_client, config=config)
        raise WebSearchCustomApiKeyInvalid(
            provider=provider,
            reason="该 provider 不支持 API key 搜索源",
        )

    def _base_url(self, provider: SearchProviderName) -> str:
        if provider == SearchProviderName.EXA:
            return self.exa_base_url
        if provider == SearchProviderName.TAVILY:
            return self.tavily_base_url
        if provider == SearchProviderName.ANYSEARCH:
            return self.anysearch_base_url
        if provider == SearchProviderName.BAIDU_QIANFAN:
            return self.baidu_qianfan_base_url
        raise WebSearchCustomApiKeyInvalid(
            provider=provider,
            reason="该 provider 不支持 API key 搜索源",
        )
