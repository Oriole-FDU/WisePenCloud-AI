from sandbox.core.lazy import make_getattr

__all__ = [
    "DefaultToolTextFormatter",
    "ExecutionResultRepository",
    "InMemoryExecutionResultRepository",
    "Result",
    "ResultSinkAdapter",
    "ToolTextFormatter",
    "ToolTextFormatterConfig",
]

_LAZY = {
    "DefaultToolTextFormatter": "sandbox.ResultReturn.formatters.tool_text_formatter",
    "ToolTextFormatter": "sandbox.ResultReturn.formatters.tool_text_formatter",
    "ToolTextFormatterConfig": "sandbox.ResultReturn.formatters.tool_text_formatter",
    "ExecutionResultRepository": "sandbox.ResultReturn.returnResult",
    "InMemoryExecutionResultRepository": "sandbox.ResultReturn.returnResult",
    "Result": "sandbox.ResultReturn.returnResult",
    "ResultSinkAdapter": "sandbox.ResultReturn.returnResult",
}

__getattr__ = make_getattr(_LAZY)
