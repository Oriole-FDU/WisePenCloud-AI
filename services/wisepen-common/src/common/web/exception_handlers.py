from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from common.core.domain import R, ResultCode
from common.core.exceptions import ServiceException
from common.logger import log_error, log_fail


def setup_global_exception_handlers(app: FastAPI, is_dev: bool = False):
    @app.exception_handler(ServiceException)
    async def service_exception_handler(request: Request, e: ServiceException):
        log_fail("业务处理", e, code=e.code)
        status_code = 500 if e.code >= 50000 else 200
        return JSONResponse(
            status_code=status_code,
            content=R.fail(error_code=ResultCode.SYSTEM_ERROR, custom_msg=e.msg).model_dump()
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, e: RequestValidationError):
        err_msg = e.errors()[0].get("msg") if e.errors() else "参数错误"
        log_fail("请求参数校验", err_msg, path=request.url.path)
        return JSONResponse(
            status_code=400,
            content=R.fail(ResultCode.PARAM_ERROR, custom_msg=err_msg).model_dump()
        )

    @app.exception_handler(Exception)
    async def global_exception_handler(request: Request, e: Exception):
        log_error("系统内部处理", e, path=request.url.path)
        error_msg = f"System Error: {str(e)}" if is_dev else ResultCode.SYSTEM_ERROR.msg
        return JSONResponse(
            status_code=500,
            content=R.fail(ResultCode.SYSTEM_ERROR, custom_msg=error_msg).model_dump()
        )