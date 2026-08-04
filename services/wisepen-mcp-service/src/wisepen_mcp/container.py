from dependency_injector import containers, providers
import httpx
from v2.nacos import NacosNamingService

from common.cloud.service_discovery import ServiceDiscovery
from common.http.rpc_client import RpcClient
from wisepen_mcp.core.config.app_settings import settings
from wisepen_mcp.core.config.bootstrap_settings import bootstrap_settings
from wisepen_mcp.core.config.nacos import nacos_client_manager
from wisepen_mcp.capabilities.web_search import WebSearchService
from wisepen_mcp.utils.ranking.presets import (
    build_web_search_ranking_pipeline,
)
from wisepen_mcp.capabilities.web_search.services import (
    SearchPipeline,
    SearchSourceFactory,
)
from wisepen_mcp.capabilities.web_search.services.providers import (
    DdgSearcher,
    FourGetSearcher,
    PlatformDefaultSearcher,
)
from wisepen_mcp.capabilities.web_search.services.providers.base import (
    SearchProviderConfig,
)
from wisepen_mcp.service_client import AIAssetClient, RagServiceClient


async def _provide_nacos_naming() -> NacosNamingService:
    return await nacos_client_manager.get_naming_client()


def _build_platform_default_searcher(
    http_client: httpx.AsyncClient,
) -> PlatformDefaultSearcher:
    return PlatformDefaultSearcher(
        fourget_searcher=FourGetSearcher(
            http_client=http_client,
            config=SearchProviderConfig(
                base_url=settings.WEB_SEARCH_FOURGET_BASE_URL,
            ),
        ),
        ddg_searcher=DdgSearcher(),
    )


class Container(containers.DeclarativeContainer):
    service_discovery = providers.Singleton(
        ServiceDiscovery,
        naming_client_provider=providers.Object(_provide_nacos_naming),
        group_name=bootstrap_settings.NACOS_GROUP,
        default_strategy=settings.RPC_LB_STRATEGY,
        cache_ttl_seconds=settings.SERVICE_DISCOVERY_CACHE_TTL_SECONDS,
    )
    rpc_client = providers.Singleton(
        RpcClient,
        discovery=service_discovery,
        from_source_secret=settings.FROM_SOURCE_SECRET,
        timeout=settings.RPC_DEFAULT_TIMEOUT,
        retries=settings.RPC_DEFAULT_RETRIES,
        default_strategy=settings.RPC_LB_STRATEGY,
    )
    ai_asset_client = providers.Singleton(
        AIAssetClient,
        rpc=rpc_client,
    )
    rag_service_client = providers.Singleton(
        RagServiceClient,
        rpc=rpc_client,
    )
    web_search_http_client = providers.Singleton(
        httpx.AsyncClient,
        timeout=httpx.Timeout(settings.WEB_SEARCH_HTTP_TIMEOUT_SECONDS),
    )
    platform_default_searcher = providers.Singleton(
        _build_platform_default_searcher,
        http_client=web_search_http_client,
    )
    web_search_source_factory = providers.Singleton(
        SearchSourceFactory,
        http_client=web_search_http_client,
        platform_default_searcher=platform_default_searcher,
        exa_base_url=settings.WEB_SEARCH_EXA_BASE_URL,
        tavily_base_url=settings.WEB_SEARCH_TAVILY_BASE_URL,
        anysearch_base_url=settings.WEB_SEARCH_ANYSEARCH_BASE_URL,
        baidu_qianfan_base_url=settings.WEB_SEARCH_BAIDU_QIANFAN_BASE_URL,
        tinyfish_base_url=settings.WEB_SEARCH_TINYFISH_BASE_URL,
        firecrawl_base_url=settings.WEB_SEARCH_FIRECRAWL_BASE_URL,
    )
    web_search_ranking_pipeline = providers.Singleton(
        build_web_search_ranking_pipeline,
    )
    web_search_pipeline = providers.Singleton(
        SearchPipeline,
        ranking_pipeline=web_search_ranking_pipeline,
    )
    web_search_service = providers.Singleton(
        WebSearchService,
        search_pipeline=web_search_pipeline,
        source_factory=web_search_source_factory,
    )


container = Container()
