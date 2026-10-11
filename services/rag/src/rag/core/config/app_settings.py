"""从 Nacos 加载 ACL 所需的运行配置。"""

import asyncio
import threading

import yaml
from common.logger import error
from pydantic import BaseModel, ConfigDict

from .nacos import nacos_client_manager


class AppSettings(BaseModel):
    """当前阶段只保留 ACL 权威读取、投影和 Kafka 消费所需字段。"""

    model_config = ConfigDict(extra="ignore")

    MONGODB_URL: str
    MONGODB_DB_NAME: str
    RESOURCE_MONGODB_DB_NAME: str | None = None

    KAFKA_ENABLED: bool = False
    KAFKA_BOOTSTRAP_SERVERS: str = ""
    KAFKA_RESOURCE_ACL_RECALC_TOPIC: str = "wispen-resource-acl-recalc-topic"
    KAFKA_RAG_ACL_RECALC_GROUP_ID: str = "wispen-rag-v4-acl-recalc-group"

    @property
    def resource_mongodb_db_name(self) -> str:
        return self.RESOURCE_MONGODB_DB_NAME or self.MONGODB_DB_NAME


def _run_async(coroutine):
    result = None
    caught: Exception | None = None

    def run() -> None:
        nonlocal result, caught
        try:
            result = asyncio.run(coroutine)
        except Exception as exc:  # noqa: BLE001
            caught = exc

    thread = threading.Thread(target=run)
    thread.start()
    thread.join()
    if caught is not None:
        raise caught
    return result


def load_settings() -> AppSettings:
    try:
        raw_yaml = _run_async(nacos_client_manager.pull_config())
        values = yaml.safe_load(raw_yaml) if raw_yaml else {}
        return AppSettings(**(values or {}))
    except Exception as exc:
        error("nacos app config pull failed.", exc=exc)
        raise


settings = load_settings()
