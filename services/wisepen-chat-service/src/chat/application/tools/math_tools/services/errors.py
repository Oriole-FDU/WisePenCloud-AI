from __future__ import annotations


class MathSolverError(Exception):
    """数学求解工具的可预期失败。"""

    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.message = message
        self.retryable = retryable