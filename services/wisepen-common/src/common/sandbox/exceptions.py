"""
统一沙箱异常体系。

SandboxException 是所有沙箱相关错误的基类，同时在 sandbox-service、
aio-gateway 和 chat-service 的 SandboxProvider/AioGatewayProvider 中使用。
"""
from __future__ import annotations

from enum import Enum
from typing import Optional


class SandboxErrorCode(str, Enum):
    """沙箱错误码，字符串值直接可放入 HTTP 响应体。"""

    # --- 通用 ---
    UNKNOWN = "unknown"
    INTERNAL = "internal_error"
    TIMEOUT = "timeout"

    # --- 请求验证 ---
    MISSING_PARAM = "missing_param"
    INVALID_PARAM = "invalid_param"
    MISSING_TENANT = "missing_tenant"

    # --- 路径隔离 ---
    PATH_TRAVERSAL = "path_traversal"
    PATH_OUTSIDE_WORKSPACE = "path_outside_workspace"
    PATH_EMPTY = "path_empty"

    # --- 文件系统 ---
    FILE_NOT_FOUND = "file_not_found"
    FILE_READ_ERROR = "file_read_error"
    FILE_WRITE_ERROR = "file_write_error"
    DIR_NOT_FOUND = "dir_not_found"
    STRING_NOT_FOUND = "string_not_found"

    # --- Shell 执行 ---
    SHELL_EXEC_ERROR = "shell_exec_error"
    SHELL_TIMEOUT = "shell_timeout"
    DOCKER_NOT_FOUND = "docker_not_found"

    # --- 容器队列 ---
    QUEUE_NOT_ENABLED = "queue_not_enabled"
    QUEUE_NO_IDLE = "queue_no_idle"
    QUEUE_FULL = "queue_full"
    CONTAINER_START_FAILED = "container_start_failed"
    CONTAINER_UNHEALTHY = "container_unhealthy"
    FILE_SYNC_FAILED = "file_sync_failed"

    # --- 提供者通信 ---
    PROVIDER_ERROR = "provider_error"
    PROVIDER_UNREACHABLE = "provider_unreachable"
    RPC_ERROR = "rpc_error"


# 错误码 → HTTP 状态码映射
_ERROR_HTTP_STATUS: dict[SandboxErrorCode, int] = {
    SandboxErrorCode.UNKNOWN: 500,
    SandboxErrorCode.INTERNAL: 500,
    SandboxErrorCode.TIMEOUT: 504,
    SandboxErrorCode.MISSING_PARAM: 400,
    SandboxErrorCode.INVALID_PARAM: 400,
    SandboxErrorCode.MISSING_TENANT: 401,
    SandboxErrorCode.PATH_TRAVERSAL: 403,
    SandboxErrorCode.PATH_OUTSIDE_WORKSPACE: 403,
    SandboxErrorCode.PATH_EMPTY: 400,
    SandboxErrorCode.FILE_NOT_FOUND: 404,
    SandboxErrorCode.FILE_READ_ERROR: 500,
    SandboxErrorCode.FILE_WRITE_ERROR: 500,
    SandboxErrorCode.DIR_NOT_FOUND: 404,
    SandboxErrorCode.STRING_NOT_FOUND: 400,
    SandboxErrorCode.SHELL_EXEC_ERROR: 500,
    SandboxErrorCode.SHELL_TIMEOUT: 504,
    SandboxErrorCode.DOCKER_NOT_FOUND: 500,
    SandboxErrorCode.QUEUE_NOT_ENABLED: 503,
    SandboxErrorCode.QUEUE_NO_IDLE: 429,
    SandboxErrorCode.QUEUE_FULL: 429,
    SandboxErrorCode.CONTAINER_START_FAILED: 500,
    SandboxErrorCode.CONTAINER_UNHEALTHY: 500,
    SandboxErrorCode.FILE_SYNC_FAILED: 500,
    SandboxErrorCode.PROVIDER_ERROR: 502,
    SandboxErrorCode.PROVIDER_UNREACHABLE: 502,
    SandboxErrorCode.RPC_ERROR: 502,
}


class SandboxException(Exception):
    """统一的沙箱异常基类，所有沙箱相关错误使用此类或其子类抛出。"""

    def __init__(
        self,
        code: SandboxErrorCode,
        message: str,
        detail: Optional[str] = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail = detail
        self.retryable = retryable

    @property
    def http_status(self) -> int:
        return _ERROR_HTTP_STATUS.get(self.code, 500)

    def to_dict(self) -> dict:
        d: dict = {"error": self.code.value, "message": self.message}
        if self.detail:
            d["detail"] = self.detail
        return d

    def __str__(self) -> str:
        s = f"[{self.code.value}] {self.message}"
        if self.detail:
            s += f" ({self.detail})"
        return s

    # ---- 工厂方法 (便捷构造) ----

    @classmethod
    def path_traversal(cls, detail: str = "") -> "SandboxException":
        return cls(SandboxErrorCode.PATH_TRAVERSAL, "path traversal denied", detail)

    @classmethod
    def path_outside_workspace(cls, detail: str = "") -> "SandboxException":
        return cls(SandboxErrorCode.PATH_OUTSIDE_WORKSPACE,
                   "access outside workspace denied", detail)

    @classmethod
    def path_empty(cls) -> "SandboxException":
        return cls(SandboxErrorCode.PATH_EMPTY, "empty path")

    @classmethod
    def missing_tenant(cls, detail: str = "") -> "SandboxException":
        return cls(SandboxErrorCode.MISSING_TENANT, "missing X-User-Id or X-Session-Id", detail)

    @classmethod
    def file_not_found(cls, path: str = "") -> "SandboxException":
        return cls(SandboxErrorCode.FILE_NOT_FOUND, "file not found", path)

    @classmethod
    def queue_not_enabled(cls) -> "SandboxException":
        return cls(SandboxErrorCode.QUEUE_NOT_ENABLED,
                   "container queue not enabled (set SANDBOX_QUEUE_ENABLE=1)")

    @classmethod
    def queue_no_idle(cls, total: int = 0, max_total: int = 0) -> "SandboxException":
        return cls(SandboxErrorCode.QUEUE_NO_IDLE,
                   f"no idle containers (total={total}, max={max_total})", retryable=True)

    @classmethod
    def container_start_failed(cls, detail: str = "") -> "SandboxException":
        return cls(SandboxErrorCode.CONTAINER_START_FAILED, "container start failed", detail)

    @classmethod
    def docker_error(cls, operation: str, detail: str = "") -> "SandboxException":
        return cls(SandboxErrorCode.CONTAINER_START_FAILED,
                   f"docker {operation} failed", detail)

    @classmethod
    def file_sync_failed(cls, detail: str = "") -> "SandboxException":
        return cls(SandboxErrorCode.FILE_SYNC_FAILED, "file sync failed", detail)

    @classmethod
    def shell_error(cls, detail: str = "") -> "SandboxException":
        return cls(SandboxErrorCode.SHELL_EXEC_ERROR, "shell execution failed", detail)

    @classmethod
    def shell_timeout(cls, detail: str = "") -> "SandboxException":
        return cls(SandboxErrorCode.SHELL_TIMEOUT, "shell execution timed out", detail)

    @classmethod
    def provider_error(cls, detail: str = "") -> "SandboxException":
        return cls(SandboxErrorCode.PROVIDER_ERROR, "provider error", detail, retryable=True)

    @classmethod
    def provider_unreachable(cls, detail: str = "") -> "SandboxException":
        return cls(SandboxErrorCode.PROVIDER_UNREACHABLE, "provider unreachable", detail, retryable=True)

    @classmethod
    def file_write_error(cls, detail: str = "") -> "SandboxException":
        return cls(SandboxErrorCode.FILE_WRITE_ERROR, "file write failed", detail)

    @classmethod
    def file_read_error(cls, detail: str = "") -> "SandboxException":
        return cls(SandboxErrorCode.FILE_READ_ERROR, "file read failed", detail)

    @classmethod
    def string_not_found(cls, detail: str = "") -> "SandboxException":
        return cls(SandboxErrorCode.STRING_NOT_FOUND, "old_str not found in file", detail)

    @classmethod
    def from_exc(cls, code: SandboxErrorCode, message: str, exc: BaseException) -> "SandboxException":
        return cls(code, message, f"{type(exc).__name__}: {exc}")
