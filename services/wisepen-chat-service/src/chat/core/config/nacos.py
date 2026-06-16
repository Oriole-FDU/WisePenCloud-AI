from chat.core.config.bootstrap_settings import bootstrap_settings
from common.cloud.nacos_client import NacosClientManager

nacos_client_manager = NacosClientManager(bootstrap_settings)