"""Scholar 外部失败的 HTTP 映射；应用和路由统一在 main 装配。"""

from common.core.domain import R, ResultCode
from common.core.exceptions import ServiceException
from common.web.exception_handlers import setup_global_exception_handlers
from elastic_transport import TransportError
from elasticsearch import ApiError
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from redis.exceptions import RedisError

from scholar.domain.interfaces.provider_error import ProviderError


def setup_exception_handlers(app: FastAPI) -> None:
    setup_global_exception_handlers(app)

    @app.exception_handler(ServiceException)
    async def business_error(request: Request, exc: ServiceException):
        # 保留能力不可用等实际业务码；Common 默认处理器会统一改写成 SYSTEM_ERROR。
        return JSONResponse(
            status_code=200, content=R(code=exc.code, msg=exc.msg).model_dump()
        )

    @app.exception_handler(RedisError)
    @app.exception_handler(TransportError)
    @app.exception_handler(ApiError)
    @app.exception_handler(ProviderError)
    async def dependency_error(request: Request, exc):
        return JSONResponse(
            status_code=503,
            # HTTP/MCP 将真实外部错误交给调用方决策，不再抹平成分类字符串。
            content=R.fail(ResultCode.SYSTEM_ERROR, str(exc)).model_dump(),
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
