import os
import warnings
from contextlib import asynccontextmanager

warnings.filterwarnings(
    "ignore",
    category=DeprecationWarning,
    module=r"websockets\.legacy",
)

import uvicorn
from beanie import init_beanie
from fastapi import FastAPI
from pymongo import AsyncMongoClient

from common.logger import error, info, setup_logging_intercept
from common.observability import instrument_fastapi_app, setup_observability
from common.web.exception_handlers import setup_global_exception_handlers
from common.web.middleware import SecurityHeaderMiddleware
from rag.api.router import api_router
from rag.api.endpoints import navigation as navigation_endpoints
from rag.api.endpoints import resources as resources_endpoints
from rag.container import container
from rag.core.config.app_settings import settings
from rag.core.config.bootstrap_settings import bootstrap_settings
from rag.core.config.nacos import nacos_client_manager
from rag.domain.entities import (
    RagAclProjectionDocument,
    RagContentPartDocument,
    RagContextIndexingDocument,
    RagContentRevisionDocument,
    RagGraphExtractionDocument,
    RagPageDocument,
    RagProjectionCheckpointDocument,
    RagSectionDocument,
    RagSectionReadingBlockDocument,
    RagSourceRefDocument,
)

setup_logging_intercept(bootstrap_settings.LOG_LEVEL)
setup_observability(
    service_name=bootstrap_settings.SERVICE_NAME,
    environment=bootstrap_settings.PROFILE,
)

no_proxy = ",".join(
    filter(
        None,
        [
            os.environ.get("NO_PROXY") or os.environ.get("no_proxy") or "",
            "localhost, 127.0.0.1",
            settings.QDRANT_HOST,
        ],
    )
)
os.environ["no_proxy"] = no_proxy
os.environ["NO_PROXY"] = no_proxy


@asynccontextmanager
async def lifespan(app: FastAPI):
    info("service starting.", service=bootstrap_settings.SERVICE_NAME)

    mongo_client = AsyncMongoClient(settings.MONGODB_URL)
    await init_beanie(
        database=mongo_client[settings.MONGODB_DB_NAME],
        document_models=[
            RagAclProjectionDocument,
            RagContentRevisionDocument,
            RagContentPartDocument,
            RagPageDocument,
            RagContextIndexingDocument,
            RagGraphExtractionDocument,
            RagSectionReadingBlockDocument,
            RagProjectionCheckpointDocument,
            RagSectionDocument,
            RagSourceRefDocument,
        ],
    )
    await container.neo4j_driver().verify_connectivity()
    await container.knowledge_graph_repository().initialize()

    try:
        await nacos_client_manager.register_instance()
    except Exception as exception:
        error("nacos instance register failed.", exc=exception)

    consumers = (
        container.acl_kafka_consumer(),
        container.document_kafka_consumer(),
        container.resource_deleted_kafka_consumer(),
    )
    for consumer in consumers:
        await consumer.start()

    info(
        "service ready.",
        service=bootstrap_settings.SERVICE_NAME,
        port=bootstrap_settings.SERVICE_PORT,
    )
    yield

    info("service stopping.", service=bootstrap_settings.SERVICE_NAME)
    for consumer in consumers:
        await consumer.stop()

    await container.redis_client().aclose()
    await container.qdrant_client().close()
    await container.neo4j_driver().close()
    await mongo_client.close()

    try:
        await nacos_client_manager.deregister_instance()
    except Exception as exception:
        error("nacos instance deregister failed.", exc=exception)


container.wire(modules=[navigation_endpoints, resources_endpoints])

app = FastAPI(
    title=bootstrap_settings.APP_NAME,
    lifespan=lifespan,
    docs_url="/docs",
)
instrument_fastapi_app(app)
app.add_middleware(
    SecurityHeaderMiddleware,
    from_source_secret=settings.FROM_SOURCE_SECRET,
)
setup_global_exception_handlers(app, is_dev=bootstrap_settings.IS_DEV)
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
