from common.core.config.bootstrap_settings import BootstrapSettings


class GatewayBootstrapSettings(BootstrapSettings):
    APP_NAME: str = "WisePen AIO Gateway"
    SERVICE_NAME: str = "wisepen-aio-service"
    SERVICE_PORT: int = 8001


bootstrap_settings = GatewayBootstrapSettings()
