from typing import Optional

from common.core.domain import IErrorCode


class ServiceException(Exception):
    """业务异常基类"""
    def __init__(self, error_code: IErrorCode, custom_msg: Optional[str] = None):
        self.code = error_code.code
        self.msg = custom_msg if custom_msg else error_code.msg
        super().__init__(self.msg)


class ServiceUnavailableError(Exception):
    """服务不可用异常"""
    def __init__(self, service_name: str, group_name: Optional[str] = None):
        self.service_name = service_name
        self.group_name = group_name
        super().__init__(
            f"No healthy instance for service '{service_name}' (group={group_name})"
        )


class RpcError(Exception):
    """跨服务 RPC 调用失败的统一异常类型"""
    def __init__(
        self,
        service_name: str,
        path: str,
        *,
        status: Optional[int] = None,
        code: Optional[int] = None,
        msg: Optional[str] = None,
        cause: Optional[BaseException] = None,
    ):
        self.service_name = service_name
        self.path = path
        self.status = status
        self.code = code
        self.msg = msg or (str(cause) if cause else "rpc call failed")
        self.cause = cause
        super().__init__(
            f"RPC {service_name}{path} failed: status={status} code={code} msg={self.msg}"
        )