from __future__ import annotations

from dataclasses import dataclass

from chat.application.tools.search_tools.web_search.core.errors import WebSearchInternalError
from chat.application.tools.search_tools.web_search.core.runtime_context import WebSearchRuntimeConfig
from chat.application.tools.search_tools.web_search.core.sources import (
    PlatformDefaultSearchSource,
    PlatformMemberSearchSource,
    WebSearchSourceKind,
)
from chat.application.tools.search_tools.web_search.factories.integration_searcher_factory import (
    IntegrationSearcherFactory,
)
from chat.application.tools.search_tools.web_search.searchers import ProviderSearcher


@dataclass(frozen=True, slots=True)
class WebSearchPlatformSourceFactory:
    """构造平台搜索源，会员源与 custom 源保持独立。"""

    platform_default_searcher: ProviderSearcher
    integration_searcher_factory: IntegrationSearcherFactory

    def build(
            self,
            config: WebSearchRuntimeConfig,
    ) -> PlatformDefaultSearchSource | PlatformMemberSearchSource:
        if config.source_kind == WebSearchSourceKind.PLATFORM_DEFAULT:
            return PlatformDefaultSearchSource(searcher=self.platform_default_searcher)

        if config.source_kind == WebSearchSourceKind.PLATFORM_MEMBER:
            if config.provider is None or not config.api_key:
                raise WebSearchInternalError(
                    provider=config.provider,
                    reason="platform_member 缺少 provider 或平台 API key",
                )
            searcher = self.integration_searcher_factory.build(
                provider=config.provider,
                api_key=config.api_key,
                source_id=config.source_id,
            )
            return PlatformMemberSearchSource(
                provider=config.provider,
                source_id=config.source_id,
                searcher=searcher,
                api_key=config.api_key,
            )

        raise WebSearchInternalError(
            provider=config.provider,
            reason="运行时配置不是平台搜索源",
        )
