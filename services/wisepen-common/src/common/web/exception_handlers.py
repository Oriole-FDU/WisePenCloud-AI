from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from common.logger import log_fail, log_error

from common.core.domain import ResultCode, R
from common.core.exceptions import ServiceException


def setup_global_exception_handlers(app: FastAPI, is_dev: bool = False):
    @app.exception_handler(ServiceException)
    async def service_exception_handler(request: Request, exc: ServiceException):
        log_fail("业务处理", exc, code=exc.code)
        status_code = 500 if exc.code >= 50000 else 200
        return JSONResponse(
            status_code=status_code,
            content=R.fail(error_code=ResultCode.SYSTEM_ERROR, custom_msg=exc.msg).model_dump()
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError):
        err_msg = exc.errors()[0].get("msg") if exc.errors() else "参数错误"
        log_fail("请求参数校验", err_msg, path=request.url.path)
        return JSONResponse(
            status_code=400,
            content=R.fail(ResultCode.PARAM_ERROR, custom_msg=err_msg).model_dump()
        )

    @app.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception):
        log_error("系统内部处理", exc, path=request.url.path)
        error_msg = f"System Error: {str(exc)}" if is_dev else ResultCode.SYSTEM_ERROR.msg
        return JSONResponse(
            status_code=500,
            content=R.fail(ResultCode.SYSTEM_ERROR, custom_msg=error_msg).model_dump()
        )