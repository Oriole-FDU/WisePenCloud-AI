from common.core.config.bootstrap_settings import BootstrapSettings


class RagBootstrapSettings(BootstrapSettings):
    APP_NAME: str = "WisePen RAG Service v2"
    SERVICE_NAME: str = "wisepen-rag-service"
    SERVICE_PORT: int = 19913


bootstrap_settings = RagBootstrapSettings()
