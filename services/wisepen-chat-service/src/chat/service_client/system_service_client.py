from enum import IntEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel

from common.core.exceptions import RpcError
from common.http.rpc_client import RpcClient
from common.logger import warn


_DEFAULT_SERVICE_NAME = "wisepen-system-service"
_SAVE_LOG_PATH = "/internal/system/log/saveLog"


class BusinessType(IntEnum):
    """与 Java BusinessType 的 ordinal 对齐。"""

    OTHER = 0
    SELECT = 1
    INSERT = 2
    UPDATE = 3
    DELETE = 4
    EXPORT = 5
    IMPORT = 6


class SysOperLog(BaseModel):
    """Java SysOperLogDTO 的传输契约，不对应 Python 数据库集合。"""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    title: str
    business_type: BusinessType
    method: str
    req_method: str
    oper_user_id: int
    oper_url: str
    oper_ip: str
    oper_param: str
    json_result: str
    status: Literal[0, 1]
    error_msg: str | None = None
    # Java JacksonConfiguration 将毫秒时间戳反序列化为 LocalDateTime。
    oper_time: int
    cost_time: int


class SystemClient:
    def __init__(
        self, rpc: RpcClient, *, service_name: str = _DEFAULT_SERVICE_NAME, timeout: float = 5.0,
    ) -> None:
        self._rpc = rpc
        self._service_name = service_name
        self._timeout = timeout

    async def save_operation_log(self, record: SysOperLog, request_id: str) -> None:
        try:
            saved = await self._rpc.post(
                self._service_name,
                _SAVE_LOG_PATH,
                json=record.model_dump(mode="json", by_alias=True),
                timeout=self._timeout,
                # Java 接口每次调用都会插入；超时后重试可能产生重复记录。
                retries=0,
            )
            if saved is not True:
                raise RpcError(
                    service_name=self._service_name, path=_SAVE_LOG_PATH,
                    msg="operation log was not saved",
                )
        except Exception as exc:
            # 与 Java AsyncLogService 一致，日志服务不可用不影响主业务。
            # 不输出响应体或异常内容，避免远端错误回显日志参数。
            warn(
                "operation log save failed.", requestId=request_id,
                operUserId=record.oper_user_id, dependency=self._service_name,
                errorType=type(exc).__name__,
            )
