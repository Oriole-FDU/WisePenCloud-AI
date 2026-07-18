from dependency_injector import containers, providers
from v2.nacos import NacosNamingService

from common.cloud.service_discovery import ServiceDiscovery
from common.http.rpc_client import RpcClient
from wisepen_mcp.core.config.app_settings import settings
from wisepen_mcp.core.config.bootstrap_settings import bootstrap_settings
from wisepen_mcp.core.config.nacos import nacos_client_manager
from wisepen_mcp.service_client import AIAssetClient


async def _provide_nacos_naming() -> NacosNamingService:
    return await nacos_client_manager.get_naming_client()


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


container = Container()
