"""RAG HTTP adapter 的业务异常响应契约。"""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from common.core.domain import R
from common.core.exceptions import ServiceException
from common.logger import warn


def setup_rag_exception_handler(app: FastAPI) -> None:
    """保留 RAG 业务错误码，供调用方区分缺失、失效与依赖失败。"""

    @app.exception_handler(ServiceException)
    async def service_exception_handler(
            request: Request,
            error: ServiceException,
    ) -> JSONResponse:
        warn(
            "rag business exception handled.",
            code=error.code,
            path=request.url.path,
        )
        return JSONResponse(
            status_code=500 if error.code >= 50000 else 200,
            content=R(code=error.code, msg=error.msg).model_dump(),
        )
