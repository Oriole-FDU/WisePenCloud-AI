from __future__ import annotations

from typing import Any

__all__ = [
    "DefaultToolTextFormatter",
    "ExecutionResultRepository",
    "InMemoryExecutionResultRepository",
    "Result",
    "ResultSinkAdapter",
    "ToolTextFormatter",
    "ToolTextFormatterConfig",
]


def __getattr__(name: str) -> Any:
    if name in ("DefaultToolTextFormatter", "ToolTextFormatter", "ToolTextFormatterConfig"):
        from sandbox.ResultReturn.formatters.tool_text_formatter import (
            DefaultToolTextFormatter,
            ToolTextFormatter,
            ToolTextFormatterConfig,
        )

        return locals()[name]

    if name in ("ExecutionResultRepository", "InMemoryExecutionResultRepository", "Result", "ResultSinkAdapter"):
        from sandbox.ResultReturn.returnResult import (
            ExecutionResultRepository,
            InMemoryExecutionResultRepository,
            Result,
            ResultSinkAdapter,
        )

        return locals()[name]

    raise AttributeError(name)
