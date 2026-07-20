"""
沙箱错误码定义 — 实现 IErrorCode，用于 ServiceException。
"""
from __future__ import annotations

from common.core.domain.enums import IErrorCode


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
