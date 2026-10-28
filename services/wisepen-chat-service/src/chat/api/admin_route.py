import json
from collections.abc import Awaitable, Callable
from ipaddress import ip_address
from time import perf_counter, time
from uuid import uuid4

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from fastapi.routing import APIRoute
from starlette.background import BackgroundTasks

from chat.domain.error_codes import ChatErrorCode
from chat.service_client.system_service_client import BusinessType, SysOperLog
from common.core.domain import R, ResultCode
from common.core.exceptions import ServiceException
from common.logger import warn
from common.security.exceptions import PermissionException
from common.security.context import SecurityContextHolder


class AdminRoute(APIRoute):
    """管理员响应保留真实错误码，失败路径不输出请求体或底层异常。"""

    def get_route_handler(self) -> Callable[[Request], Awaitable[Response]]:
        handler = super().get_route_handler()

        async def safe_handler(request: Request) -> Response:
            request.state.admin_request_id = uuid4().hex
            started_at = perf_counter()
            try:
                response = await handler(request)
            except RequestValidationError:
                status, code, message = 400, ResultCode.PARAM_ERROR.code, ResultCode.PARAM_ERROR.msg
            except ServiceException as exc:
                code, message = exc.code, exc.msg
                if isinstance(exc, PermissionException):
                    status = 401 if code == 401 else 403
                elif code in {ChatErrorCode.PROVIDER_NOT_FOUND.code, ChatErrorCode.MODEL_NOT_FOUND.code,
                              ChatErrorCode.MODEL_MAPPING_NOT_FOUND.code}:
                    status = 404
                elif code in {ChatErrorCode.CONFIG_VERSION_CONFLICT.code, ChatErrorCode.DEFAULT_MODEL_UNAVAILABLE.code,
                              ChatErrorCode.PROVIDER_ALREADY_EXISTS.code, ChatErrorCode.MODEL_ALREADY_EXISTS.code,
                              ChatErrorCode.MODEL_MAPPING_ALREADY_EXISTS.code}:
                    status = 409
                elif code == ChatErrorCode.ADMIN_STORAGE_UNAVAILABLE.code:
                    status = 503
                else:
                    status = 400
            except Exception:
                status, code, message = 500, ResultCode.SYSTEM_ERROR.code, ResultCode.SYSTEM_ERROR.msg
            else:
                self._attach_operation_log(request, response, started_at, succeeded=True)
                return response

            # 不传 exc/e.errors()，验证异常和数据库错误可能含明文密钥或整个文档。
            warn("admin request rejected.", requestId=request.state.admin_request_id, code=code)
            response = JSONResponse(status_code=status, content=R(code=code, msg=message).model_dump())
            self._attach_operation_log(request, response, started_at, succeeded=False)
            return response

        return safe_handler

    def _attach_operation_log(
        self, request: Request, response: Response, started_at: float, *, succeeded: bool,
    ) -> None:
        client = getattr(request.state, "admin_log_client", None)
        if client is None:
            return
        try:
            # 只读取事务提交后生成的摘要；失败请求不读取请求体或校验异常中的输入。
            fields = dict(getattr(request.state, "admin_operation", {}))
            fields.setdefault("requestId", request.state.admin_request_id)
            for key in ("changes", "changedFields"):
                if key in fields:
                    fields[key] = json.loads(fields[key])
            forwarded_ip = request.headers.get("x-forwarded-for", "").split(",", 1)[0].strip()
            try:
                oper_ip = str(ip_address(forwarded_ip))
            except ValueError:
                oper_ip = request.client.host if request.client else "unknown"
            record = SysOperLog(
                title=self.summary or self.name,
                business_type=BusinessType.INSERT if self.name.startswith("create_") else BusinessType.UPDATE,
                method=f"{self.endpoint.__module__}.{self.endpoint.__name__}()",
                req_method=request.method,
                oper_user_id=int(SecurityContextHolder.get_user_id()),
                oper_url=request.url.path,
                oper_ip=oper_ip,
                oper_param=json.dumps(fields, ensure_ascii=False),
                json_result=response.body.decode("utf-8"),
                status=0 if succeeded else 1,
                error_msg=None if succeeded else json.loads(response.body)["msg"],
                oper_time=int(time() * 1000),
                cost_time=max(0, int((perf_counter() - started_at) * 1000)),
            )
            background = BackgroundTasks()
            # 操作日志先发送，避免其他后台任务失败使它无法执行。
            background.add_task(client.save_operation_log, record, request.state.admin_request_id)
            if response.background is not None:
                background.add_task(response.background)
            response.background = background
        except Exception as exc:
            warn(
                "operation log capture failed.", requestId=request.state.admin_request_id,
                errorType=type(exc).__name__,
            )
