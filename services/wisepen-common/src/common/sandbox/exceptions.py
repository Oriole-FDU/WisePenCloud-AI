"""
统一沙箱异常体系 — 继承 common ServiceException 基类。
"""
from __future__ import annotations

from common.core.domain.enums import IErrorCode
from common.core.exceptions import ServiceException


class SandboxErrorCode(IErrorCode):
    """沙箱错误码。元组 (int_code, str_msg)。"""

    UNKNOWN = (500, "sandbox unknown error")
    INTERNAL = (500, "sandbox internal error")
    TIMEOUT = (504, "sandbox operation timeout")

    MISSING_PARAM = (400, "missing required parameter")
    INVALID_PARAM = (400, "invalid parameter")
    MISSING_TENANT = (401, "missing X-User-Id or X-Session-Id")

    PATH_TRAVERSAL = (403, "path traversal denied")
    PATH_OUTSIDE_WORKSPACE = (403, "access outside workspace denied")
    PATH_EMPTY = (400, "empty path")

    FILE_NOT_FOUND = (404, "file not found")
    FILE_READ_ERROR = (500, "file read failed")
    FILE_WRITE_ERROR = (500, "file write failed")
    DIR_NOT_FOUND = (404, "directory not found")
    STRING_NOT_FOUND = (400, "old_str not found in file")

    SHELL_EXEC_ERROR = (500, "shell execution failed")
    SHELL_TIMEOUT = (504, "shell execution timed out")
    DOCKER_NOT_FOUND = (500, "docker binary not found")

    QUEUE_NOT_ENABLED = (503, "container queue not enabled")
    QUEUE_NO_IDLE = (429, "no idle containers available")
    QUEUE_FULL = (429, "container pool is full")
    CONTAINER_START_FAILED = (500, "container start failed")
    CONTAINER_UNHEALTHY = (500, "container unhealthy")
    FILE_SYNC_FAILED = (500, "file sync failed")

    PROVIDER_ERROR = (502, "sandbox provider error")
    PROVIDER_UNREACHABLE = (502, "sandbox provider unreachable")
    RPC_ERROR = (502, "sandbox rpc call failed")


class SandboxException(ServiceException):
    """沙箱异常，继承 ServiceException (code + msg)。"""

    def __init__(self, error_code: SandboxErrorCode, custom_msg: str = None):
        super().__init__(error_code, custom_msg)
        self._error_code = error_code

    @property
    def error_code(self) -> SandboxErrorCode:
        return self._error_code

    @property
    def http_status(self) -> int:
        return self.code

    def to_dict(self) -> dict:
        d: dict = {"error": self._error_code.name, "message": self.msg}
        return d

    def __str__(self) -> str:
        return f"[{self._error_code.name}] {self.msg}"

    # ---- 工厂方法 (便捷构造) ----

    @classmethod
    def path_traversal(cls, detail: str = "") -> "SandboxException":
        return cls(SandboxErrorCode.PATH_TRAVERSAL, detail or None)

    @classmethod
    def path_outside_workspace(cls, detail: str = "") -> "SandboxException":
        return cls(SandboxErrorCode.PATH_OUTSIDE_WORKSPACE, detail or None)

    @classmethod
    def path_empty(cls) -> "SandboxException":
        return cls(SandboxErrorCode.PATH_EMPTY)

    @classmethod
    def missing_tenant(cls, detail: str = "") -> "SandboxException":
        return cls(SandboxErrorCode.MISSING_TENANT, detail or None)

    @classmethod
    def file_not_found(cls, path: str = "") -> "SandboxException":
        return cls(SandboxErrorCode.FILE_NOT_FOUND, path or None)

    @classmethod
    def queue_not_enabled(cls) -> "SandboxException":
        return cls(SandboxErrorCode.QUEUE_NOT_ENABLED)

    @classmethod
    def queue_no_idle(cls, total: int = 0, max_total: int = 0) -> "SandboxException":
        return cls(SandboxErrorCode.QUEUE_NO_IDLE,
                   f"total={total} max={max_total}")

    @classmethod
    def container_start_failed(cls, detail: str = "") -> "SandboxException":
        return cls(SandboxErrorCode.CONTAINER_START_FAILED, detail or None)

    @classmethod
    def docker_error(cls, operation: str, detail: str = "") -> "SandboxException":
        return cls(SandboxErrorCode.CONTAINER_START_FAILED,
                   f"docker {operation}: {detail}" if detail else f"docker {operation}")

    @classmethod
    def file_sync_failed(cls, detail: str = "") -> "SandboxException":
        return cls(SandboxErrorCode.FILE_SYNC_FAILED, detail or None)

    @classmethod
    def shell_error(cls, detail: str = "") -> "SandboxException":
        return cls(SandboxErrorCode.SHELL_EXEC_ERROR, detail or None)

    @classmethod
    def shell_timeout(cls, detail: str = "") -> "SandboxException":
        return cls(SandboxErrorCode.SHELL_TIMEOUT, detail or None)

    @classmethod
    def provider_error(cls, detail: str = "") -> "SandboxException":
        return cls(SandboxErrorCode.PROVIDER_ERROR, detail or None)

    @classmethod
    def provider_unreachable(cls, detail: str = "") -> "SandboxException":
        return cls(SandboxErrorCode.PROVIDER_UNREACHABLE, detail or None)

    @classmethod
    def file_write_error(cls, detail: str = "") -> "SandboxException":
        return cls(SandboxErrorCode.FILE_WRITE_ERROR, detail or None)

    @classmethod
    def file_read_error(cls, detail: str = "") -> "SandboxException":
        return cls(SandboxErrorCode.FILE_READ_ERROR, detail or None)

    @classmethod
    def string_not_found(cls, detail: str = "") -> "SandboxException":
        return cls(SandboxErrorCode.STRING_NOT_FOUND, detail or None)

    @classmethod
    def from_exc(cls, code: SandboxErrorCode, message: str,
                 exc: BaseException) -> "SandboxException":
        return cls(code, f"{message}: {type(exc).__name__}: {exc}")
