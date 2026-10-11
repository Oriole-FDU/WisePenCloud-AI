"""RAG V4 ACL 阶段服务入口。"""

import os
from contextlib import asynccontextmanager

from common.logger import error, info, setup_logging_intercept
from common.observability import instrument_fastapi_app, setup_observability
from common.web.exception_handlers import setup_global_exception_handlers
from fastapi import FastAPI

from rag.api.router import api_router
from rag.container import container
from rag.core.config.app_settings import settings
from rag.core.config.bootstrap_settings import bootstrap_settings
from rag.core.config.nacos import nacos_client_manager

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
        ],
    )
)
os.environ["no_proxy"] = no_proxy
os.environ["NO_PROXY"] = no_proxy


@asynccontextmanager
async def lifespan(_: FastAPI):
    info("service starting.", service=bootstrap_settings.SERVICE_NAME)
    consumer = None
    if settings.KAFKA_ENABLED:
        consumer = container.acl_consumer()
        await consumer.start()
    try:
        try:
            await nacos_client_manager.register_instance()
        except Exception as exc:  # noqa: BLE001
            error("nacos instance register failed.", exc=exc)
        info("service ready.", service=bootstrap_settings.SERVICE_NAME)
        yield
    finally:
        info("service stopping.", service=bootstrap_settings.SERVICE_NAME)
        if consumer is not None:
            try:
                await consumer.stop()
            except Exception as exc:  # noqa: BLE001
                error("acl kafka consumer stop failed.", exc=exc)
        try:
            await container.mongo_client().close()
        except Exception as exc:  # noqa: BLE001
            error("mongo client close failed.", exc=exc)
        try:
            await nacos_client_manager.deregister_instance()
        except Exception as exc:  # noqa: BLE001
            error("nacos instance deregister failed.", exc=exc)


app = FastAPI(title=bootstrap_settings.APP_NAME, lifespan=lifespan, docs_url="/docs")
instrument_fastapi_app(app)
setup_global_exception_handlers(app, is_dev=bootstrap_settings.IS_DEV)
app.include_router(api_router, prefix="/rag")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "rag.main:app",
        host=bootstrap_settings.SERVICE_HOST,
        port=bootstrap_settings.SERVICE_PORT,
        reload=False,
        workers=1,
    )
