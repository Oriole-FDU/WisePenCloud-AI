from v2.nacos import RegisterInstanceParam, DeregisterInstanceParam
from common.cloud.nacos_client import NacosClientManager
from common.logger import info, error
from sandbox.gateway.bootstrap import bootstrap_settings

nacos_client_manager = NacosClientManager(bootstrap_settings)

_MCP_SERVICE_NAME = "wisepen-sandbox-mcp-service"


async def register_mcp_service() -> None:
    """向 Nacos 额外注册 wisepen-sandbox-mcp-service。
    Chat 服务通过该服务名发现沙箱 MCP 端点。
    """
    naming = await nacos_client_manager.get_naming_client()
    host = nacos_client_manager._resolve_host()
    metadata = {"preserved.register.source": "PYTHON_FASTAPI"}
    try:
        await naming.register_instance(
            request=RegisterInstanceParam(
                service_name=_MCP_SERVICE_NAME,
                group_name=bootstrap_settings.NACOS_GROUP,
                ip=host,
                port=bootstrap_settings.SERVICE_PORT,
                metadata=metadata,
                healthy=True,
                ephemeral=True,
            )
        )
        info("nacos mcp service registered.", service=_MCP_SERVICE_NAME, addr=f"{host}:{bootstrap_settings.SERVICE_PORT}")
    except Exception as e:
        error("nacos mcp service register failed.", service=_MCP_SERVICE_NAME, exc=e)


async def deregister_mcp_service() -> None:
    """从 Nacos 注销 wisepen-sandbox-mcp-service。"""
    naming = await nacos_client_manager.get_naming_client()
    host = nacos_client_manager._resolve_host()
    try:
        await naming.deregister_instance(
            request=DeregisterInstanceParam(
                service_name=_MCP_SERVICE_NAME,
                group_name=bootstrap_settings.NACOS_GROUP,
                ip=host,
                port=bootstrap_settings.SERVICE_PORT,
                ephemeral=True,
            )
        )
        info("nacos mcp service deregistered.", service=_MCP_SERVICE_NAME)
    except Exception as e:
        error("nacos mcp service deregister failed.", service=_MCP_SERVICE_NAME)
