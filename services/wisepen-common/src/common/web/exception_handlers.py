from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from common.logger import error, warn

from common.core.domain import ResultCode, R
from common.core.exceptions import ServiceException


def setup_global_exception_handlers(app: FastAPI, is_dev: bool = False):
    @app.exception_handler(ServiceException)
    async def service_exception_handler(request: Request, e: ServiceException):
        warn("business exception handled.", code=e.code, path=request.url.path, custom_msg=e.msg)
        status_code = 500 if e.code >= 50000 else 200
        return JSONResponse(
            status_code=status_code,
            content=R.fail(error_code=ResultCode.SYSTEM_ERROR, custom_msg=e.msg).model_dump()
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, e: RequestValidationError):
        err_msg = e.errors()[0].get("msg") if e.errors() else "参数错误"
        warn("request validation rejected.", path=request.url.path, exc=e)
        return JSONResponse(
            status_code=400,
            content=R.fail(ResultCode.PARAM_ERROR, custom_msg=err_msg).model_dump()
        )

    @app.exception_handler(Exception)
    async def global_exception_handler(request: Request, e: Exception):
        error("internal error handled.", path=request.url.path, exc=e)
        error_msg = f"System Error: {str(e)}" if is_dev else ResultCode.SYSTEM_ERROR.msg
        return JSONResponse(
            status_code=500,
            content=R.fail(ResultCode.SYSTEM_ERROR, custom_msg=error_msg).model_dump()
        )
