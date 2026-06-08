from typing import Any, Dict

from chat.application.tools.math_solver.services.errors import MathSolverError
from chat.application.tools.math_solver.services.formatting import (
    format_math_solver_error,
    format_math_solver_result,
)
from chat.application.tools.math_solver.services.models import (
    MathSolverRequest,
    SAGE_MATH_TASKS,
)
from chat.application.tools.math_solver.services.python_runtime.enums import PYTHON_MATH_TASKS
from chat.application.tools.math_solver.services.python_runtime.service import PythonMathSolverService
from chat.domain.interfaces.tool import BaseTool

_DESCRIPTION = (
    "Performs deterministic ordinary mathematical computation using Python math libraries "
    "such as SymPy, NumPy, SciPy, and mpmath. Use this for ordinary algebra, calculus, "
    "equations, matrices, combinatorics, probability/statistics, numeric roots, and numeric "
    "optimization. This tool does not execute arbitrary Python code, does not use pandas, "
    "does not draw plots, and does not call SageMath."
)

_REQUEST_FIELDS = (
    "task",
    "expression",
    "equation",
    "equations",
    "variable",
    "variables",
    "point",
    "order",
    "lower_bound",
    "upper_bound",
    "matrix",
    "matrix_b",
    "vector",
    "n",
    "k",
    "probability",
    "lower",
    "upper",
)

_MATRIX_ENTRY_SCHEMA = {"type": ["integer", "number", "string"]}

_TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "task": {
            "type": "string",
            "enum": sorted(PYTHON_MATH_TASKS),
            "description": "Python math task to execute.",
        },
        "expression": {"type": "string"},
        "equation": {"type": "string"},
        "equations": {"type": "array", "items": {"type": "string"}},
        "variable": {"type": "string"},
        "variables": {"type": "array", "items": {"type": "string"}},
        "point": {"type": "string"},
        "order": {"type": "integer"},
        "lower_bound": {
            "type": "string",
            "description": "Lower integration bound for definite_integral.",
        },
        "upper_bound": {
            "type": "string",
            "description": "Upper integration bound for definite_integral.",
        },
        "matrix": {
            "type": "array",
            "items": {"type": "array", "items": _MATRIX_ENTRY_SCHEMA},
        },
        "matrix_b": {
            "type": "array",
            "items": {"type": "array", "items": _MATRIX_ENTRY_SCHEMA},
        },
        "vector": {"type": "array", "items": _MATRIX_ENTRY_SCHEMA},
        "n": {"type": "integer"},
        "k": {"type": "integer"},
        "probability": {"type": "string"},
        "lower": {
            "type": "string",
            "description": "Alias for lower_bound on definite_integral; lower limit for summation/numeric ranges.",
        },
        "upper": {
            "type": "string",
            "description": "Alias for upper_bound on definite_integral; upper limit for summation/numeric ranges.",
        },
    },
    "required": ["task"],
    "additionalProperties": False,
}


class PythonMathSolverTool(BaseTool):
    def __init__(self, service: PythonMathSolverService) -> None:
        self._service = service

    @property
    def name(self) -> str:
        return "python_math_solver"

    @property
    def description(self) -> str:
        return _DESCRIPTION

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return _TOOL_SCHEMA

    @property
    def namespaces(self) -> tuple[str, ...]:
        return ("math_solver",)

    async def execute(self, context: Dict[str, Any], **kwargs: Any) -> str:
        session_id = context.get("session_id")
        if not session_id:
            return "[Tool Error] Missing session_id in execution context."

        task = kwargs["task"]
        if task in SAGE_MATH_TASKS:
            return format_math_solver_error(
                self.name,
                task,
                "advanced exact math is not supported by python_math_solver. Use sage_math_solver.",
                False,
            )

        request_data = {field: kwargs.get(field) for field in _REQUEST_FIELDS}
        if task == "definite_integral":
            if request_data["lower_bound"] is None:
                request_data["lower_bound"] = request_data["lower"]
            if request_data["upper_bound"] is None:
                request_data["upper_bound"] = request_data["upper"]
            if request_data["lower_bound"] is None or request_data["upper_bound"] is None:
                return format_math_solver_error(
                    self.name,
                    task,
                    "definite_integral requires both lower_bound and upper_bound. "
                    "You may pass lower/upper as aliases, but the integration limits must not be missing.",
                    False,
                )

        request = MathSolverRequest(**request_data)

        try:
            result = await self._service.solve(request)
            return format_math_solver_result(self.name, result)
        except MathSolverError as e:
            return format_math_solver_error(
                self.name,
                request.task,
                e.message,
                e.retryable,
            )

    async def close(self) -> None:
        await self._service.close()
