"""RAG V3 服务启动入口。"""

import os
import warnings
from contextlib import asynccontextmanager

warnings.filterwarnings(
    "ignore", category=DeprecationWarning, module=r"websockets\.legacy"
)

from common.logger import error, info, setup_logging_intercept
from common.observability import instrument_fastapi_app, setup_observability

from rag_v3.core.config.bootstrap_settings import bootstrap_settings

setup_logging_intercept(bootstrap_settings.LOG_LEVEL)
setup_observability(
    service_name=bootstrap_settings.SERVICE_NAME,
    environment=bootstrap_settings.PROFILE,
)

import uvicorn
from beanie import init_beanie
from common.web.exception_handlers import setup_global_exception_handlers
from common.web.middleware import SecurityHeaderMiddleware
from fastapi import FastAPI

from rag_v3.api.endpoints import reading as reading_endpoints
from rag_v3.api.endpoints import retrieval as retrieval_endpoints
from rag_v3.api.kafka import (
    AclRecalculatePayload,
    DocumentReadyPayload,
    KafkaEventConsumer,
    ResourceDestroyPayload,
    validated_handler,
)
from rag_v3.api.router import api_router
from rag_v3.container import container
from rag_v3.core.config.app_settings import settings
from rag_v3.core.config.nacos import nacos_client_manager
from rag_v3.domain.entities import (
    DocChunkEntity,
    DocumentRevisionEntity,
    GraphEdgeProjectionEntity,
    GraphNodeProjectionEntity,
    ResourceAclEntity,
    ResourceIndexStateEntity,
    TextGraphEvidenceEntity,
)

no_proxy = ",".join(
    filter(
        None,
        [
            os.environ.get("NO_PROXY") or os.environ.get("no_proxy") or "",
            "localhost, 127.0.0.1",
        ],
    )
)
os.environ["no_proxy"] = no_proxy
os.environ["NO_PROXY"] = no_proxy


@asynccontextmanager
async def lifespan(_: FastAPI):
    info("service starting.", service=bootstrap_settings.SERVICE_NAME)
    mongo_client = container.mongo_client()
    await init_beanie(
        database=mongo_client[settings.MONGODB_DB_NAME],
        document_models=[
            ResourceIndexStateEntity,
            ResourceAclEntity,
            DocumentRevisionEntity,
            DocChunkEntity,
            GraphNodeProjectionEntity,
            GraphEdgeProjectionEntity,
            TextGraphEvidenceEntity,
        ],
    )
    kafka_consumers: list[KafkaEventConsumer] = []
    if settings.KAFKA_ENABLED:
        handlers = [
            (
                DocumentReadyPayload,
                settings.KAFKA_DOCUMENT_READY_TOPIC,
                settings.KAFKA_RAG_DOCUMENT_READY_GROUP_ID,
                container.document_ready_handler().handle,
            ),
            (
                AclRecalculatePayload,
                settings.KAFKA_RESOURCE_ACL_RECALC_TOPIC,
                settings.KAFKA_RAG_ACL_RECALC_GROUP_ID,
                container.acl_recalculate_handler().handle,
            ),
            (
                ResourceDestroyPayload,
                settings.KAFKA_RESOURCE_PHYSICAL_DESTROY_TOPIC,
                settings.KAFKA_RAG_RESOURCE_DESTROY_GROUP_ID,
                container.resource_destroy_handler().handle,
            ),
        ]
        try:
            for payload_model, topic, group_id, handler in handlers:
                consumer = KafkaEventConsumer(
                    bootstrap_servers=settings.KAFKA_BOOTSTRAP_SERVERS,
                    topic=topic,
                    group_id=group_id,
                    handler=validated_handler(payload_model, handler),
                    retry_delay_seconds=settings.KAFKA_RAG_RETRY_DELAY_SECONDS,
                    max_delivery_attempts=settings.KAFKA_RAG_MAX_DELIVERY_ATTEMPTS,
                    dead_letter_topic=settings.KAFKA_RAG_DEAD_LETTER_TOPIC,
                )
                await consumer.start()
                kafka_consumers.append(consumer)
        except Exception:
            for consumer in reversed(kafka_consumers):
                await consumer.stop()
            raise
    try:
        await nacos_client_manager.register_instance()
    except Exception as exc:  # noqa: BLE001  # pragma: no cover - 注册失败不阻断本地启动
        error("nacos instance register failed.", exc=exc)
    info("service ready.", service=bootstrap_settings.SERVICE_NAME)
    yield
    info("service stopping.", service=bootstrap_settings.SERVICE_NAME)
    for consumer in reversed(kafka_consumers):
        try:
            await consumer.stop()
        except Exception as exc:  # noqa: BLE001
            error("kafka consumer stop failed.", exc=exc)
    try:
        await mongo_client.close()
    except Exception as exc:  # noqa: BLE001  # pragma: no cover - 退出时尽力关闭客户端
        error("mongo client close failed.", exc=exc)
    try:
        await container.openai_client().close()
    except Exception as exc:  # noqa: BLE001  # pragma: no cover - 退出时尽力关闭客户端
        error("openai client close failed.", exc=exc)
    try:
        await container.qdrant_client().close()
    except Exception as exc:  # noqa: BLE001  # pragma: no cover - 退出时尽力关闭客户端
        error("qdrant client close failed.", exc=exc)
    neo4j_driver = container.neo4j_driver()
    if neo4j_driver is not None:
        try:
            await neo4j_driver.close()
        except Exception as exc:  # noqa: BLE001  # pragma: no cover - 退出时尽力关闭客户端
            error("neo4j driver close failed.", exc=exc)
    try:
        await nacos_client_manager.deregister_instance()
    except Exception as exc:  # noqa: BLE001  # pragma: no cover - 注销失败不阻断退出
        error("nacos instance deregister failed.", exc=exc)


app = FastAPI(title=bootstrap_settings.APP_NAME, lifespan=lifespan, docs_url="/docs")
instrument_fastapi_app(app)
app.add_middleware(
    SecurityHeaderMiddleware, from_source_secret=settings.FROM_SOURCE_SECRET
)
setup_global_exception_handlers(app, is_dev=bootstrap_settings.IS_DEV)
app.include_router(api_router, prefix="/rag")
container.wire(modules=[reading_endpoints, retrieval_endpoints])


if __name__ == "__main__":
    uvicorn.run(
        "rag_v3.main:app",
        host=bootstrap_settings.SERVICE_HOST,
        port=bootstrap_settings.SERVICE_PORT,
        reload=False,
        workers=1,
    )
