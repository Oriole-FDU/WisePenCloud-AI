from common.core.config.bootstrap_settings import BootstrapSettings


class SandboxV1BootstrapSettings(BootstrapSettings):
    """沙箱 v1 core 启动前即可读取的最小配置。

    这些值用于日志、观测、HTTP 监听和 Nacos 定位；完整池配置稍后由
    AppSettings 从本地 YAML 或 Nacos 加载。
    """

    APP_NAME: str = "WisePen Sandbox V1 Core Service"
    SERVICE_NAME: str = "wisepen-sandbox-v1-service"
    SERVICE_PORT: int = 19915


bootstrap_settings = SandboxV1BootstrapSettings()
