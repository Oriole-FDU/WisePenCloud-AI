"""HTTP 应用工厂；配置加载和客户端生命周期由 main 管理。"""

from common.core.domain import R, ResultCode
from common.core.exceptions import ServiceException
from common.web.exception_handlers import setup_global_exception_handlers
from common.web.middleware import SecurityHeaderMiddleware
from elastic_transport import TransportError
from elasticsearch import ApiError
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from redis.exceptions import RedisError

from scholar.api.router import api_router


def create_app(*, from_source_secret: str, lifespan=None) -> FastAPI:
    app = FastAPI(title="WisePen Scholar Service", lifespan=lifespan)
    app.add_middleware(SecurityHeaderMiddleware, from_source_secret=from_source_secret)
    setup_global_exception_handlers(app)
    app.include_router(api_router, prefix="/scholar")

    @app.exception_handler(ServiceException)
    async def business_error(request: Request, exc: ServiceException):
        # 保留能力不可用等实际业务码；Common 默认处理器会统一改写成 SYSTEM_ERROR。
        return JSONResponse(
            status_code=200, content=R(code=exc.code, msg=exc.msg).model_dump()
        )

    @app.exception_handler(RedisError)
    @app.exception_handler(TransportError)
    @app.exception_handler(ApiError)
    async def dependency_error(request: Request, exc):
        return JSONResponse(
            status_code=503,
            content=R.fail(
                ResultCode.SYSTEM_ERROR, "Scholar storage is unavailable"
            ).model_dump(),
        )

    @app.exception_handler(TimeoutError)
    async def request_timeout(request: Request, exc):
        return JSONResponse(
            status_code=504,
            content={
                "code": 504,
                "msg": "Scholar request budget exceeded",
                "data": None,
            },
        )

    return app
