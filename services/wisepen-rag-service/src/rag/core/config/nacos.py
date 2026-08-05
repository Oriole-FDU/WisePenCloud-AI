from common.cloud.nacos_client import NacosClientManager
from rag.core.config.bootstrap_settings import bootstrap_settings


nacos_client_manager = NacosClientManager(bootstrap_settings)
