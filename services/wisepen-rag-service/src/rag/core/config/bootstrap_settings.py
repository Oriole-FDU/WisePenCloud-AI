"""RAG 服务在拉取 Nacos 配置前所需的最小配置。"""

from common.core.config.bootstrap_settings import BootstrapSettings


class RagBootstrapSettings(BootstrapSettings):
    APP_NAME: str = "WisePen RAG Service"
    SERVICE_NAME: str = "wisepen-rag-service"
    SERVICE_PORT: int = 19906


bootstrap_settings = RagBootstrapSettings()
