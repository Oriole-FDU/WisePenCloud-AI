from common.logger import error, info, setup_logging_intercept
from common.observability import setup_observability

from rag.core.config.bootstrap_settings import bootstrap_settings

# 在读取 Nacos 业务配置之前完成日志桥接与 OTel SDK 初始化。
setup_logging_intercept(bootstrap_settings.LOG_LEVEL)
setup_observability(
    service_name=bootstrap_settings.SERVICE_NAME,
    environment=bootstrap_settings.PROFILE,
)

from contextlib import asynccontextmanager

import uvicorn
from beanie import init_beanie
from common.observability import instrument_fastapi_app
from common.web.exception_handlers import setup_global_exception_handlers
from common.web.middleware import SecurityHeaderMiddleware
from fastapi import FastAPI

from rag.api.endpoints import expand as expand_endpoints
from rag.api.endpoints import locate as locate_endpoints
from rag.api.endpoints import read as read_endpoints
from rag.api.exception_handlers import setup_rag_exception_handler
from rag.api.router import api_router
from rag.container import container
from rag.core.config.app_settings import settings
from rag.core.config.nacos import nacos_client_manager
from rag.domain.entities import (
    ContentRevisionEntity,
    GenerationArtifactEntity,
    ReadingBlockEntity,
    ResourceAclEntity,
    ResourceIndexStateEntity,
    SectionEntity,
    SourcePartEntity,
    SourceRefEntity,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    info("service starting.", service=bootstrap_settings.SERVICE_NAME)

    mongo_client = container.mongo_client()
    await init_beanie(
        database=mongo_client[settings.MONGODB_DB_NAME],
        document_models=[
            ResourceIndexStateEntity,
            ContentRevisionEntity,
            SourcePartEntity,
            SectionEntity,
            ReadingBlockEntity,
            SourceRefEntity,
            ResourceAclEntity,
            GenerationArtifactEntity,
        ],
    )
    info("beanie initialized.", db=settings.MONGODB_DB_NAME)

    await container.retrieval_index_writer().ensure_collection()
    await container.knowledge_graph_repository().initialize()
    await container.graph_acl_writer().initialize()

    if settings.RAG_KNOWLEDGE_GRAPH_ENABLED:
        await container.neo4j_driver().verify_connectivity()

    consumers = (
        container.document_ready_consumer(),
        container.acl_recalculate_consumer(),
        container.resource_destroy_consumer(),
    )
    started = []
    try:
        for consumer in consumers:
            await consumer.start()
            started.append(consumer)
        try:
            await nacos_client_manager.register_instance()
        except Exception as e:
            error("nacos instance register failed.", exc=e)

        info(
            "service ready.",
            service=bootstrap_settings.SERVICE_NAME,
            port=bootstrap_settings.SERVICE_PORT,
        )
        yield
    finally:
        info("service stopping.", service=bootstrap_settings.SERVICE_NAME)

        for consumer in reversed(started):
            await consumer.stop()

        try:
            await container.contextual_text_client().close()
        except Exception as e:
            error("contextual text client close failed.", exc=e)
        try:
            await container.graph_query_client().close()
        except Exception as e:
            error("graph query client close failed.", exc=e)
        try:
            await container.embedding_client().close()
        except Exception as e:
            error("embedding client close failed.", exc=e)
        try:
            await container.zero_entropy_client().close()
        except Exception as e:
            error("zero entropy client close failed.", exc=e)
        try:
            await container.redis_client().aclose()
        except Exception as e:
            error("redis client close failed.", exc=e)
        try:
            await container.qdrant_client().close()
        except Exception as e:
            error("qdrant client close failed.", exc=e)
        try:
            await container.neo4j_driver().close()
        except Exception as e:
            error("neo4j driver close failed.", exc=e)
        try:
            await container.mongo_client().close()
        except Exception as e:
            error("mongo client close failed.", exc=e)
        try:
            await nacos_client_manager.deregister_instance()
        except Exception as e:
            error("nacos instance deregister failed.", exc=e)


container.wire(
    modules=[
        locate_endpoints,
        read_endpoints,
        expand_endpoints,
    ]
)

app = FastAPI(
    title=bootstrap_settings.APP_NAME,
    docs_url="/docs",
    lifespan=lifespan,
)
instrument_fastapi_app(app)
app.add_middleware(
    SecurityHeaderMiddleware,
    from_source_secret=settings.FROM_SOURCE_SECRET,
)
setup_global_exception_handlers(app, is_dev=bootstrap_settings.IS_DEV)
setup_rag_exception_handler(app)
app.include_router(api_router, prefix="/internal/rag")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": bootstrap_settings.SERVICE_NAME}


if __name__ == "__main__":
    uvicorn.run(
        "rag.main:app",
        host=bootstrap_settings.SERVICE_HOST,
        port=bootstrap_settings.SERVICE_PORT,
        reload=False,
        workers=1,
    )
