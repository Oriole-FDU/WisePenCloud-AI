from pathlib import Path
from typing import Optional
from dotenv import find_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict


class BootstrapSettings(BaseSettings):
    """
    各微服务通用引导配置，从各自的 .env 文件读取
    仅定义 class，不在此处实例化，各服务在自己的 config/ 目录中完成实例化
    """

    # 服务基础信息
    APP_NAME: str = "WisePen Service"
    SERVICE_NAME: str = "wisepen-service"
    SERVICE_HOST: str = "127.0.0.1"
    SERVICE_PORT: int = 8000

    # 运行模式与日志
    DEV: bool = False
    LOG_LEVEL: str = "INFO"

    # Nacos 配置中心接入信息
    NACOS_SERVER_ADDR: str
    NACOS_NAMESPACE_ID: str
    NACOS_GROUP: str = "DEFAULT_GROUP"
    NACOS_DATA_ID: str
    NACOS_USERNAME: Optional[str] = None
    NACOS_PASSWORD: Optional[str] = None

    model_config = SettingsConfigDict(
        env_file=find_dotenv(usecwd=True),
        env_file_encoding="utf-8",
        extra="ignore",
    )

