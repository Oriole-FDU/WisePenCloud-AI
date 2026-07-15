from common.cloud.nacos_client import NacosClientManager
from sandbox.gateway.bootstrap import bootstrap_settings

nacos_client_manager = NacosClientManager(bootstrap_settings)
