"""沿用 Chat/Sandbox 的引导、配置、容器和 HTTP 生命周期。"""

import asyncio
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path
from time import monotonic

from common.logger import error, info, warning, setup_logging_intercept
from common.cloud.nacos_client import NacosClientManager
from common.observability import setup_observability
from scholar.core.config.bootstrap_settings import bootstrap_settings

# BYOK 密钥会进入请求栈，异常日志保留错误和回溯，但不展开局部变量。
setup_logging_intercept(bootstrap_settings.LOG_LEVEL, diagnose=False)
setup_observability(
    service_name=bootstrap_settings.SERVICE_NAME, environment=bootstrap_settings.PROFILE
)

import uvicorn
from common.core.domain import R
from common.core.exceptions import ServiceException
from common.observability import instrument_fastapi_app
from common.web.exception_handlers import setup_global_exception_handlers
from common.web.middleware import SecurityHeaderMiddleware
from common.utils.ai_clients.embedding import EmbeddingClient
import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from redis.exceptions import RedisError, TimeoutError as RedisTimeout
from qdrant_client.http.exceptions import ResponseHandlingException, UnexpectedResponse

from scholar.api.endpoints import embeddings, health, papers
from scholar.api.router import api_router
from scholar.container import container
from scholar.core.config.app_settings import settings
from scholar.core.config.nacos import nacos_client_manager
from scholar.core.config.policy_settings import ScholarPolicyStore, validate_policy_sources, watch_policies
from scholar.domain.errors import ProviderBusy, ProviderError, ScholarErrorCode, provider_operation
from scholar.domain.models.fields import AcademicField
from scholar.ml.specter import DEFAULT_MODELS, DIMENSIONS, QUERY_MODEL, SpecterEncoder

container.config.from_dict(settings.model_dump(exclude={"SCHOLAR_POLICIES"}))
container.policy_store.override(ScholarPolicyStore(settings.SCHOLAR_POLICIES))
container.wire(modules=[health, papers])


def setup_exception_handlers(app: FastAPI) -> None:
    setup_global_exception_handlers(app)

    @app.exception_handler(ServiceException)
    async def business_error(request: Request, exc: ServiceException):
        # 保留能力不可用等实际业务码；Common 默认处理器会统一改写成 SYSTEM_ERROR。
        return JSONResponse(
            status_code=503 if exc.code == ScholarErrorCode.CAPABILITY_UNAVAILABLE.code else 200,
            content=R(code=exc.code, msg=exc.msg).model_dump(),
        )

    @app.exception_handler(RedisError)
    @app.exception_handler(ProviderError)
    async def dependency_error(request: Request, exc):
        cause = exc.__cause__ if isinstance(exc, ProviderError) and exc.__cause__ is not None else exc
        if isinstance(cause, ResponseHandlingException):
            cause = cause.source
        # 限流、依赖 HTTP 拒绝和超时保留各自状态，不能统一伪装成 503。
        if isinstance(exc, ProviderBusy):
            status = 429
        elif isinstance(cause, httpx.HTTPStatusError):
            status = cause.response.status_code
        elif isinstance(cause, UnexpectedResponse):
            status = cause.status_code
        elif isinstance(cause, (TimeoutError, httpx.TimeoutException, RedisTimeout)):
            status = 504
        else:
            status = 503
        warning("Scholar dependency failed.", path=request.url.path, status_code=status, error=str(exc))
        return JSONResponse(
            status_code=status,
            # HTTP/MCP 将真实外部错误交给调用方决策，不再抹平成分类字符串。
            content=R(code=status, msg=str(exc)).model_dump(),
        )

    @app.exception_handler(TimeoutError)
    async def request_timeout(request: Request, exc):
        message = f"{request.url.path} timed out: {str(exc) or type(exc).__name__}"
        warning("Scholar request timed out.", path=request.url.path, error=message)
        return JSONResponse(
            status_code=504,
            content={
                "code": 504,
                "msg": message,
                "data": None,
            },
        )


@asynccontextmanager
async def lifespan(app):
    info("service starting.", service=bootstrap_settings.SERVICE_NAME)
    policy_store = container.policy_store()
    capabilities = {
        "search_sources": container.search_bindings.kwargs.keys(),
        "lookup_sources": container.lookup_bindings.kwargs.keys(),
        "discovery_sources": container.discovery_bindings.kwargs.keys(),
    }
    validate_policy_sources(policy_store.current, **capabilities)
    openalex_http = container.openalex_http()
    huggingface_http = container.huggingface_http()
    s2_http = container.s2_http()
    unpaywall_http = container.unpaywall_http()
    try:
        # 同一启动入口加载一次查询模型；预热和线上编码路由使用同一个实例。
        app.state.encoder = SpecterEncoder(
            Path(settings.SCHOLAR_EMBEDDING_MODELS_PATH or DEFAULT_MODELS),
            query=True,
            threads=settings.SCHOLAR_EMBEDDING_CPU_THREADS,
        )
        async with AsyncExitStack() as lifecycle:
            started = monotonic()
            # 原双库检索曾因串行冷读取连续超时；单库混合检索仍给首次索引读取预留两倍预算。
            info("arXiv startup warmup started.", timeout_seconds=settings.SCHOLAR_REQUEST_TIMEOUT_SECONDS * 3)
            try:
                async with asyncio.timeout(settings.SCHOLAR_REQUEST_TIMEOUT_SECONDS * 2):
                    # lifespan 完成前尚未监听端口；ASGI 直达同一编码路由，Qdrant 仍访问真实依赖。
                    async with container.embedding_api().with_options(
                        http_client=httpx.AsyncClient(transport=httpx.ASGITransport(app=app)),
                    ) as warmup_api:
                        result = await container.arxiv_searcher(
                            embedding_client=EmbeddingClient(warmup_api, model=QUERY_MODEL, dimensions=DIMENSIONS),
                        ).search(
                            query="machine learning",
                            field=AcademicField.COMPUTER_SCIENCE,
                            published_after=None,
                            published_before=None,
                            limit=settings.SCHOLAR_SEARCH_CANDIDATES,
                        )
                        # 线上允许编码失败时返回文本结果；启动检查必须确认完整混合链路可用。
                        if result.partial_error is not None:
                            raise ProviderError(f"arxiv startup warmup incomplete: {result.partial_error}")
            except (ProviderError, TimeoutError) as exc:
                error(
                    "arXiv startup warmup failed.", exc=exc,
                    stage=provider_operation.get(), elapsed_seconds=round(monotonic() - started, 3),
                )
                raise
            info("arXiv startup warmup completed.", papers=len(result.papers), elapsed_seconds=round(monotonic() - started, 3))
            await container.s2_transport().start()
            lifecycle.push_async_callback(container.s2_transport().aclose)
            if bootstrap_settings.NACOS_ENABLED:
                # 监听客户端属于服务事件循环，不复用导入配置时临时线程内的客户端。
                await lifecycle.enter_async_context(watch_policies(
                    policy_store, NacosClientManager(bootstrap_settings), **capabilities
                ))
                await nacos_client_manager.register_instance()
                lifecycle.push_async_callback(nacos_client_manager.deregister_instance)
            yield
    finally:
        await openalex_http.aclose()
        await huggingface_http.aclose()
        await s2_http.aclose()
        await unpaywall_http.aclose()
        await container.embedding_api().close()
        await container.qdrant().close()
        await container.redis().aclose()


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
