from common.core.config.bootstrap_settings import BootstrapSettings


class SandboxBootstrapSettings(BootstrapSettings):
    """wisepen-sandbox-service 引导配置。"""

    APP_NAME: str = "WisePen Sandbox Service"
    SERVICE_NAME: str = "wisepen-sandbox-service"
    SERVICE_PORT: int = 19905


bootstrap_settings = SandboxBootstrapSettings()
