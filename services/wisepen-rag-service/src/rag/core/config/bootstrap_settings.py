from common.core.config.bootstrap_settings import BootstrapSettings


class RagBootstrapSettings(BootstrapSettings):
    APP_NAME: str = "WisePen RAG Service"
    SERVICE_NAME: str = "wisepen-rag-service"
    SERVICE_PORT: int = 19912


bootstrap_settings = RagBootstrapSettings()
