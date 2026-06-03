from common.logger import setup_logging_intercept, log_event, log_error
from aio_gateway.settings import bootstrap_settings
setup_logging_intercept(bootstrap_settings.LOG_LEVEL)

import os
import uvicorn
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from aio_gateway.nacos import nacos_client_manager
from aio_gateway.settings import settings
from aio_gateway.api.router import api_router
from common.web.middleware import SecurityHeaderMiddleware
from common.web.exception_handlers import setup_global_exception_handlers

no_proxy = ",".join(filter(None, [
    os.environ.get("NO_PROXY") or os.environ.get("no_proxy") or "",
    "localhost, 127.0.0.1"
]))
os.environ["no_proxy"] = no_proxy
os.environ["NO_PROXY"] = no_proxy


@asynccontextmanager
async def lifespan(app: FastAPI):
    log_event(f"{bootstrap_settings.APP_NAME} 启动")

    try:
        await nacos_client_manager.register_instance()
    except Exception as e:
        log_error("Nacos 服务注册", e)

    log_event(f"{bootstrap_settings.APP_NAME} 就绪", port=bootstrap_settings.SERVICE_PORT)
    yield
    log_event(f"{bootstrap_settings.APP_NAME} 关闭")

    try:
        await nacos_client_manager.deregister_instance()
    except Exception as e:
        log_error("Nacos 服务注销", e)


app = FastAPI(title=bootstrap_settings.APP_NAME, lifespan=lifespan, docs_url="/docs")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_middleware(SecurityHeaderMiddleware, from_source_secret=settings.FROM_SOURCE_SECRET)

setup_global_exception_handlers(app, is_dev=bootstrap_settings.IS_DEV)

app.include_router(api_router, prefix="/v1/aio")

if __name__ == "__main__":
    uvicorn.run(
        "aio_gateway.main:app",
        host=bootstrap_settings.SERVICE_HOST,
        port=bootstrap_settings.SERVICE_PORT,
        reload=False,
        workers=1,
    )
