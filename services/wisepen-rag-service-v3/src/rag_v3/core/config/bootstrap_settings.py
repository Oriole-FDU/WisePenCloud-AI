"""RAG V3 在拉取 Nacos 配置前所需的最小配置。"""

from common.core.config.bootstrap_settings import BootstrapSettings


class RagBootstrapSettings(BootstrapSettings):
    APP_NAME: str = "WisePen RAG Service V3"
    SERVICE_NAME: str = "wisepen-rag-service-v3"
    SERVICE_PORT: int = 19906


bootstrap_settings = RagBootstrapSettings()
