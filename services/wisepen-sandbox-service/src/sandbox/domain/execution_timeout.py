from __future__ import annotations

from typing import Any

from common.core.exceptions import ServiceException

from sandbox.domain.error_codes import SandboxErrorCode


DEFAULT_EXECUTION_TIMEOUT_MS = 30000
MAX_EXECUTION_TIMEOUT_MS = 120000


def normalize_execution_timeout_ms(
    value: Any,
    *,
    default_timeout_ms: int = DEFAULT_EXECUTION_TIMEOUT_MS,
    max_timeout_ms: int = MAX_EXECUTION_TIMEOUT_MS,
) -> int:
    timeout_ms = default_timeout_ms if value is None else value
    if isinstance(timeout_ms, bool) or not isinstance(timeout_ms, int):
        raise ServiceException(
            SandboxErrorCode.INVALID_EXECUTION_TIMEOUT,
            "timeout_ms 必须是整数",
        )
    if timeout_ms < 1 or timeout_ms > max_timeout_ms:
        raise ServiceException(
            SandboxErrorCode.INVALID_EXECUTION_TIMEOUT,
            f"timeout_ms 必须在 1 到 {max_timeout_ms} 之间",
        )
    return timeout_ms
