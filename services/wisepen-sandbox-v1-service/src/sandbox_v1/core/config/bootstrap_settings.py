from common.core.config.bootstrap_settings import BootstrapSettings


class SandboxV1BootstrapSettings(BootstrapSettings):
    """Bootstrap settings for the sandbox pool core service."""

    APP_NAME: str = "WisePen Sandbox V1 Core Service"
    SERVICE_NAME: str = "wisepen-sandbox-v1-service"
    SERVICE_PORT: int = 19915

    # Local development does not contact Nacos unless explicitly enabled.
    NACOS_SERVER_ADDR: str = "127.0.0.1:8848"


bootstrap_settings = SandboxV1BootstrapSettings()
