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
from chat.application.tools.math_solver.services.sage_runtime.service import SageMathSolverService
from chat.domain.interfaces.tool import BaseTool

_DESCRIPTION = (
    "Performs deterministic advanced exact math through the SageMath worker. Use this only "
    "for number theory, modular arithmetic, finite fields, exact polynomial algebra over "
    "fields, and advanced exact matrix algebra such as Smith or Hermite normal forms. "
    "This tool does not execute arbitrary Sage code and does not handle ordinary symbolic "
    "or numeric tasks; use python_math_solver for ordinary computation."
)

_REQUEST_FIELDS = (
    "task",
    "integer",
    "integers",
    "base",
    "exponent",
    "modulus",
    "residues",
    "moduli",
    "polynomial",
    "polynomial_a",
    "polynomial_b",
    "variable",
    "field",
    "ring",
    "matrix",
    "operation",
    "element",
    "element_a",
    "element_b",
)

_MATRIX_ENTRY_SCHEMA = {"type": ["integer", "number", "string"]}

_TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "task": {
            "type": "string",
            "enum": sorted(SAGE_MATH_TASKS),
            "description": "SageMath task to execute.",
        },
        "integer": {"type": "integer"},
        "integers": {"type": "array", "items": {"type": "integer"}},
        "base": {"type": "integer"},
        "exponent": {"type": "integer"},
        "modulus": {"type": "integer"},
        "residues": {"type": "array", "items": {"type": "integer"}},
        "moduli": {"type": "array", "items": {"type": "integer"}},
        "polynomial": {"type": "string"},
        "polynomial_a": {"type": "string"},
        "polynomial_b": {"type": "string"},
        "variable": {"type": "string"},
        "field": {"type": "string"},
        "ring": {"type": "string"},
        "matrix": {
            "type": "array",
            "items": {"type": "array", "items": _MATRIX_ENTRY_SCHEMA},
        },
        "operation": {"type": "string"},
        "element": {"type": "string"},
        "element_a": {"type": "string"},
        "element_b": {"type": "string"},
    },
    "required": ["task"],
    "additionalProperties": False,
}


class SageMathSolverTool(BaseTool):
    def __init__(self, service: SageMathSolverService) -> None:
        self._service = service

    @property
    def name(self) -> str:
        return "sage_math_solver"

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
        if task in PYTHON_MATH_TASKS:
            return format_math_solver_error(
                self.name,
                task,
                "ordinary symbolic computation is not supported by sage_math_solver. Use python_math_solver.",
                False,
            )

        request = MathSolverRequest(
            **{field: kwargs.get(field) for field in _REQUEST_FIELDS}
        )

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
