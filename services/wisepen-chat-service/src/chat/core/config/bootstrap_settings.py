from pathlib import Path

from dotenv import load_dotenv

from common.core.config import BootstrapSettings

# 本服务实例化，从当前目录就近的 .env 文件读取配置
_env_path = Path(__file__).resolve().parents[2] / ".env"
load_dotenv(_env_path, override=False)

bootstrap_settings = BootstrapSettings()
