"""RAG V3 的 Nacos 客户端。"""

from common.cloud.nacos_client import NacosClientManager

from .bootstrap_settings import bootstrap_settings

nacos_client_manager = NacosClientManager(bootstrap_settings)
