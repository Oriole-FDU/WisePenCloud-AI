"""日志、监听与 Nacos 定位所需的引导配置。"""

from pathlib import Path

from common.core.config.bootstrap_settings import BootstrapSettings
from pydantic_settings import SettingsConfigDict

SERVICE_ROOT = Path(__file__).resolve().parents[4]


class ScholarBootstrapSettings(BootstrapSettings):
    APP_NAME: str = "WisePen Scholar Service"
    SERVICE_NAME: str = "wisepen-scholar-service"
    SERVICE_PORT: int = 19916

    model_config = SettingsConfigDict(
        env_file=SERVICE_ROOT / ".env", env_file_encoding="utf-8", extra="ignore"
    )


bootstrap_settings = ScholarBootstrapSettings()
