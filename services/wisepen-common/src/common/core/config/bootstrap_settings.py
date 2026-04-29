from typing import Literal, Optional
from dotenv import find_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict


class BootstrapSettings(BaseSettings):
    """各微服务通用引导配置基类
    仅包含从 Nacos 拉取配置之前必须就位的字段
      - SERVICE_HOST / SERVICE_PORT
      - LOG_LEVEL
      - PROFILE
      - NACOS_*

    各服务应通过子类提供 SERVICE_NAME / APP_NAME 等服务身份相关的硬编码默认值
    """

    # 服务身份（子类硬编码覆盖）
    APP_NAME: str = "WisePen Unnamed Service (Python)"
    SERVICE_NAME: str = "wisepen-unnamed-service-py"

    # 服务监听
    SERVICE_HOST: str = "127.0.0.1"
    SERVICE_PORT: int = 9200

    # 早期日志（Nacos 拉取之前生效）
    LOG_LEVEL: str = "INFO"

    # Profile
    # 本机起服务保持 dev，容器/Jenkins 部署时由 docker compose 直接覆盖为 prod
    PROFILE: Literal["dev", "prod"] = "dev"

    # Nacos 接入
    NACOS_SERVER_ADDR: str
    NACOS_NAMESPACE_ID: str = ""
    NACOS_GROUP: str = "DEFAULT_GROUP"
    NACOS_USERNAME: Optional[str] = None
    NACOS_PASSWORD: Optional[str] = None

    # 注册到 Nacos 时的本机 IP（可选）
    # 留空时由 nacos_client._resolve_host() 用 socket.gethostname 兜底，容器 / 多网卡场景下显式指定更稳。
    NACOS_REGISTER_IP: Optional[str] = None

    @property
    def NACOS_DATA_ID(self) -> str:
        """Nacos config data-id"""
        return f"{self.SERVICE_NAME}-{self.PROFILE}.yaml"

    @property
    def IS_DEV(self) -> bool:
        return self.PROFILE == "dev"

    model_config = SettingsConfigDict(
        env_file=find_dotenv(usecwd=True),
        env_file_encoding="utf-8",
        extra="ignore",
    )
