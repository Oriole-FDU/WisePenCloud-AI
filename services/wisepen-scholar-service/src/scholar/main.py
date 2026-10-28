"""沿用 Chat/Sandbox 的引导、配置、容器和 HTTP 生命周期。"""

from contextlib import asynccontextmanager
from pathlib import Path

from common.logger import info, setup_logging_intercept
from common.observability import setup_observability

from scholar.core.config.bootstrap_settings import bootstrap_settings

# BYOK 密钥会进入请求栈，异常日志保留错误和回溯，但不展开局部变量。
setup_logging_intercept(bootstrap_settings.LOG_LEVEL, diagnose=False)
setup_observability(
    service_name=bootstrap_settings.SERVICE_NAME, environment=bootstrap_settings.PROFILE
)

import uvicorn
from common.observability import instrument_fastapi_app
from common.web.middleware import SecurityHeaderMiddleware
from fastapi import FastAPI

from scholar.api.app import setup_exception_handlers
from scholar.api.endpoints import embeddings, health, papers
from scholar.api.router import api_router
from scholar.container import container
from scholar.core.config.app_settings import settings
from scholar.core.config.nacos import nacos_client_manager
from scholar.core.embedding_runtime import DEFAULT_MODELS, SpecterEncoder

container.config.from_dict(settings.model_dump())
container.wire(modules=[health, papers])


@asynccontextmanager
async def lifespan(app):
    info("service starting.", service=bootstrap_settings.SERVICE_NAME)
    openalex_http = container.openalex_http()
    huggingface_http = container.huggingface_http()
    s2_http = container.s2_http()
    # 同一启动入口加载一次查询模型；独立同步路由在线程池执行，异步 search 可调用本机 HTTP。
    app.state.encoder = SpecterEncoder(
        Path(settings.SCHOLAR_EMBEDDING_MODELS_PATH or DEFAULT_MODELS),
        query=True,
        threads=settings.SCHOLAR_EMBEDDING_CPU_THREADS,
    )
    app.state.encoder.encode(["warmup"])
    try:
        if bootstrap_settings.NACOS_ENABLED:
            await nacos_client_manager.register_instance()
        yield
    finally:
        if bootstrap_settings.NACOS_ENABLED:
            await nacos_client_manager.deregister_instance()
        await openalex_http.aclose()
        await huggingface_http.aclose()
        await s2_http.aclose()
        await container.embedding_api().close()
        await container.redis().aclose()
        await container.elasticsearch().close()


app = FastAPI(title=bootstrap_settings.APP_NAME, lifespan=lifespan)
app.add_middleware(
    SecurityHeaderMiddleware, from_source_secret=settings.FROM_SOURCE_SECRET
)
setup_exception_handlers(app)
app.include_router(api_router, prefix="/scholar")
app.include_router(embeddings.router)
instrument_fastapi_app(app)


if __name__ == "__main__":
    uvicorn.run(
        "scholar.main:app",
        host=bootstrap_settings.SERVICE_HOST,
        port=bootstrap_settings.SERVICE_PORT,
        reload=False,
        workers=1,
    )
