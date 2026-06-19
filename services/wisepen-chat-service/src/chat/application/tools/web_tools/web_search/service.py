from __future__ import annotations

from dataclasses import dataclass

import httpx
from common.logger import warn

from .errors import (
    WebSearchCustomApiKeyInvalid,
    WebSearchCustomApiKeyMissing,
    WebSearchCustomError,
    WebSearchEmptyResult,
    WebSearchInternalError,
    WebSearchNetworkError,
)
from .providers.models import ProviderSearchResponse, SearchProviderName
from .routing.endpoint_planner import resolve_endpoint_plans
from .routing.router import WebSearchRouter
from .runtime_context import WebSearchRuntimeConfig
from .searcher import WebSearchProviderSearcher
from .searchers import (
    AnySearchSearcher,
    BaseProviderSearcher,
    ExaSearcher,
    SearchProviderConfig,
    SearchProviderCredentialError,
    SearchProviderError,
    SearchProviderNetworkError,
    SerperSearcher,
    TavilySearcher,
)


@dataclass(frozen=True, slots=True)
class WebSearchCustomSource:
    """由 runtime context 快照构造出的单次 custom 搜索源。"""

    provider: SearchProviderName
    searcher: WebSearchProviderSearcher
    api_key: str


@dataclass(frozen=True, slots=True)
class WebSearchResult:
    """Web search service 的轻量返回。"""

    query: str
    responses: tuple[ProviderSearchResponse, ...]


@dataclass(frozen=True, slots=True)
class WebSearchCustomSourceFactory:
    """按已固化到 context 的运行期配置构造 custom 搜索源。"""

    http_client: httpx.AsyncClient
    exa_base_url: str
    tavily_base_url: str
    anysearch_base_url: str
    serper_base_url: str

    def build(self, config: WebSearchRuntimeConfig) -> WebSearchCustomSource:
        if not config.api_key:
            raise WebSearchCustomApiKeyMissing(
                provider=config.provider,
                reason="不存在 api key",
            )
        provider_config = SearchProviderConfig(
            base_url=self._base_url(config.provider),
            api_key=config.api_key,
            source_id=config.source_id,
        )
        searcher = self._provider_searcher(config.provider, provider_config)
        return WebSearchCustomSource(
            provider=config.provider,
            searcher=WebSearchProviderSearcher(provider_searchers={config.provider: searcher}),
            api_key=config.api_key,
        )

    def _provider_searcher(
        self,
        provider: SearchProviderName,
        config: SearchProviderConfig,
    ) -> BaseProviderSearcher:
        if provider == SearchProviderName.EXA:
            return ExaSearcher(http_client=self.http_client, config=config)
        if provider == SearchProviderName.TAVILY:
            return TavilySearcher(http_client=self.http_client, config=config)
        if provider == SearchProviderName.ANYSEARCH:
            return AnySearchSearcher(http_client=self.http_client, config=config)
        if provider == SearchProviderName.SERPER:
            return SerperSearcher(http_client=self.http_client, config=config)
        raise WebSearchCustomApiKeyInvalid(
            provider=provider,
            reason="该 provider 不支持 custom 搜索",
        )

    def _base_url(self, provider: SearchProviderName) -> str:
        if provider == SearchProviderName.EXA:
            return self.exa_base_url
        if provider == SearchProviderName.TAVILY:
            return self.tavily_base_url
        if provider == SearchProviderName.ANYSEARCH:
            return self.anysearch_base_url
        if provider == SearchProviderName.SERPER:
            return self.serper_base_url
        raise WebSearchCustomApiKeyInvalid(
            provider=provider,
            reason="该 provider 不支持 custom 搜索",
        )


class WebSearchService:
    """Web search 编排服务。

    service 不读取用户配置、不解密凭证；custom 配置必须先固化到 tool context。
    """

    def __init__(
        self,
        *,
        platform_searcher: WebSearchProviderSearcher,
        router: WebSearchRouter | None = None,
    ) -> None:
        self._platform_searcher = platform_searcher
        self._router = router or WebSearchRouter()

    async def search(
        self,
        *,
        query: str,
        max_results: int = 10,
        custom_source: WebSearchCustomSource | None = None,
        platform_provider: SearchProviderName = SearchProviderName.FOUGET_DDG,
    ) -> WebSearchResult:
        route = await self._router.route(query)

        if custom_source is not None and not custom_source.api_key.strip():
            raise WebSearchCustomApiKeyMissing(
                provider=custom_source.provider,
                reason="不存在 api key",
            )

        providers = [custom_source.provider] if custom_source is not None else [platform_provider]
        searcher = custom_source.searcher if custom_source is not None else self._platform_searcher

        responses: list[ProviderSearchResponse] = []
        for plan in resolve_endpoint_plans(route, providers):
            try:
                response = await searcher.search(
                    query=query,
                    plan=plan,
                    max_results=max_results,
                )

                if custom_source is not None and not response.results:
                    raise WebSearchEmptyResult(
                        provider=plan.provider,
                        reason="搜索源成功响应但没有返回结果",
                    )
                responses.append(response)

            except SearchProviderCredentialError as exc:
                if custom_source is None:
                    warn(
                        "web search provider skipped.",
                        provider=plan.provider,
                        endpoint=plan.endpoint,
                        reason=exc.__class__.__name__,
                        audit_message="平台搜索源凭证异常，已跳过该 provider 并尝试后续 provider。",
                    )
                    continue
                raise _to_custom_credential_error(plan.provider, exc) from exc

            except SearchProviderNetworkError as exc:
                if custom_source is None:
                    warn(
                        "web search provider skipped.",
                        provider=plan.provider,
                        endpoint=plan.endpoint,
                        reason=exc.__class__.__name__,
                        audit_message="平台搜索源网络异常，已跳过该 provider 并尝试后续 provider。",
                    )
                    continue
                raise WebSearchNetworkError(
                    provider=plan.provider,
                    reason="网络波动或连接失败",
                ) from exc

            except SearchProviderError as exc:
                if custom_source is None:
                    warn(
                        "web search provider skipped.",
                        provider=plan.provider,
                        endpoint=plan.endpoint,
                        reason=exc.__class__.__name__,
                        audit_message="平台搜索源返回错误，已跳过该 provider 并尝试后续 provider。",
                    )
                    continue
                raise WebSearchInternalError(
                    provider=plan.provider,
                    reason="内部服务错误",
                ) from exc

            except (WebSearchCustomError, WebSearchEmptyResult, WebSearchNetworkError, WebSearchInternalError):
                raise

            except Exception as exc:
                if custom_source is None:
                    warn(
                        "web search provider skipped.",
                        provider=plan.provider,
                        endpoint=plan.endpoint,
                        reason=exc.__class__.__name__,
                        audit_message="平台搜索源出现未预期异常，已跳过该 provider 并尝试后续 provider。",
                    )
                    continue
                raise WebSearchInternalError(
                    provider=plan.provider,
                    reason="内部服务错误",
                ) from exc

        return WebSearchResult(query=query, responses=tuple(responses))


def _to_custom_credential_error(
    provider: SearchProviderName,
    exc: SearchProviderCredentialError,
) -> WebSearchCustomError:
    """将底层 provider 抛出的原生凭证异常映射为用户可理解的异常。"""
    text = str(exc).lower()
    if "required" in text or "api key is required" in text:
        return WebSearchCustomApiKeyMissing(
            provider=provider,
            reason="不存在 api key",
        )
    return WebSearchCustomApiKeyInvalid(
        provider=provider,
        reason="api key 失效、过期或者额度耗尽",
    )
