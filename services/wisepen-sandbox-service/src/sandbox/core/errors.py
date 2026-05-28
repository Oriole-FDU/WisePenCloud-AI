from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional


class SandboxErrorCode(str, Enum):
    VALIDATION_FAILED = "validation_failed"
    UNSUPPORTED_SCRIPT = "unsupported_script"
    PACKAGE_NOT_FOUND = "package_not_found"
    SANDBOX_PROVIDER_ERROR = "sandbox_provider_error"
    EXECUTION_FAILED = "execution_failed"
    EXECUTION_TIMEOUT = "execution_timeout"
    INTERNAL_ERROR = "internal_error"


@dataclass(frozen=True)
class SandboxError(Exception):
    code: SandboxErrorCode
    message: str
    detail: Optional[str] = None

    def __str__(self) -> str:
        return self.message

