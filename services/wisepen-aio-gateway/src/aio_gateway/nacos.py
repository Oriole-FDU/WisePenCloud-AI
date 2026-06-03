from common.cloud.nacos_client import NacosClientManager
from aio_gateway.settings import bootstrap_settings

nacos_client_manager = NacosClientManager(bootstrap_settings)
