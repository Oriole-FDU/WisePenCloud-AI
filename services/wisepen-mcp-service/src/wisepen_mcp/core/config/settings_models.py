from common.core.config.app_settings import (
    ConfigModel,
    InternalSecuritySettings,
    RpcSettings,
    ServiceDiscoverySettings,
)
from pydantic import Field


class WebSearchProviderSettings(ConfigModel):
    base_url: str = Field(min_length=1)


class WebSearchProvidersSettings(ConfigModel):
    fourget: WebSearchProviderSettings = Field(
        default_factory=lambda: WebSearchProviderSettings.model_validate(
            {"base-url": "http://127.0.0.1:8088"}
        )
    )
    exa: WebSearchProviderSettings = Field(
        default_factory=lambda: WebSearchProviderSettings.model_validate(
            {"base-url": "https://api.exa.ai"}
        )
    )
    tavily: WebSearchProviderSettings = Field(
        default_factory=lambda: WebSearchProviderSettings.model_validate(
            {"base-url": "https://api.tavily.com"}
        )
    )
    anysearch: WebSearchProviderSettings = Field(
        default_factory=lambda: WebSearchProviderSettings.model_validate(
            {"base-url": "https://api.anysearch.com"}
        )
    )
    baidu_qianfan: WebSearchProviderSettings = Field(
        default_factory=lambda: WebSearchProviderSettings.model_validate(
            {"base-url": "https://qianfan.baidubce.com"}
        )
    )
    tinyfish: WebSearchProviderSettings = Field(
        default_factory=lambda: WebSearchProviderSettings.model_validate(
            {"base-url": "https://api.search.tinyfish.ai"}
        )
    )
    firecrawl: WebSearchProviderSettings = Field(
        default_factory=lambda: WebSearchProviderSettings.model_validate(
            {"base-url": "https://api.firecrawl.dev"}
        )
    )


class WebSearchRerankerSettings(ConfigModel):
    zero_entropy_api_key: str = Field(min_length=1)
    model: str = "zerank-2"


class WebSearchSettings(ConfigModel):
    timeout_seconds: float = Field(default=15.0, gt=0)
    providers: WebSearchProvidersSettings = Field(
        default_factory=WebSearchProvidersSettings
    )
    reranker: WebSearchRerankerSettings


class McpServiceSettings(ConfigModel):
    security: InternalSecuritySettings
    rpc: RpcSettings = Field(default_factory=RpcSettings)
    service_discovery: ServiceDiscoverySettings = Field(
        default_factory=ServiceDiscoverySettings
    )
    web_search: WebSearchSettings
