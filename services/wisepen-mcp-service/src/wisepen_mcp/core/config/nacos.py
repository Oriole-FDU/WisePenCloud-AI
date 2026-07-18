from common.cloud.nacos_client import NacosClientManager
from wisepen_mcp.core.config.bootstrap_settings import bootstrap_settings


nacos_client_manager = NacosClientManager(bootstrap_settings)
