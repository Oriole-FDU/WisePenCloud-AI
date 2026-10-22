"""Nacos 配置拉取前必须存在的 RAG 服务配置。"""

from common.core.config.bootstrap_settings import BootstrapSettings


class RagBootstrapSettings(BootstrapSettings):
    APP_NAME: str = "WisePen RAG Service"
    SERVICE_NAME: str = "wisepen-rag-service"
    SERVICE_PORT: int = 19906


bootstrap_settings = RagBootstrapSettings()
