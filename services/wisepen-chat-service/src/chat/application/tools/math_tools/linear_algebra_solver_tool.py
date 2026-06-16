from __future__ import annotations

from typing import Any

from chat.application.tools.math_tools.base_tool import MathSolverTool
from chat.application.tools.math_tools.services.solvers.linear_algebra import LinearAlgebraSolver
from chat.application.tools.math_tools.services.tasks import LinearAlgebraTask

_MATRIX_ENTRY_SCHEMA: dict[str, Any] = {"type": ["integer", "number", "string"]}

PARAMETERS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "task": {
            "type": "string",
            "enum": LinearAlgebraTask.values(),
            "description": "Linear algebra task to execute.",
        },
        "matrix": {
            "type": "array",
            "items": {"type": "array", "items": _MATRIX_ENTRY_SCHEMA},
            "description": "Input matrix.",
        },
        "matrix_b": {
            "type": "array",
            "items": {"type": "array", "items": _MATRIX_ENTRY_SCHEMA},
            "description": "Right-hand matrix for multiplication or matrix-valued linear solve.",
        },
        "vector": {
            "type": "array",
            "items": _MATRIX_ENTRY_SCHEMA,
            "description": "Right-hand vector for linear_solve.",
        },
        "power": {
            "type": "integer",
            "description": "Integer exponent for matrix_power.",
        },
    },
    "required": ["task"],
    "additionalProperties": False,
}


class LinearAlgebraSolverTool(MathSolverTool):
    """线性代数求解工具门面。"""

    def __init__(self) -> None:
        super().__init__(name="linear_algebra_solver", description=(
            "Solve deterministic linear algebra tasks: determinant, trace, rank, inverse, RREF, eigenvalues, linear systems (Ax=b), matrix multiplication, SVD, QR decomposition, null space, and matrix powers.\n"
            "\n"
            "WHEN TO TRIGGER:\n"
            "  - MUST trigger when the user asks for matrix determinant, trace, rank, inverse, or RREF.\n"
            "  - MUST trigger when computing eigenvalues, SVD, QR decomposition, or null space of a matrix.\n"
            "  - MUST trigger when solving a linear system Ax=b (pass vector) or multiplying two matrices.\n"
            "  - MUST trigger when raising a square matrix to an integer power.\n"
            "DO NOT TRIGGER when:\n"
            "  - The task is solving a nonlinear equation or optimization — use equation_solver instead.\n"
            "  - The task is differentiation, integration, or ODEs — use calculus_solver instead.\n"
            "  - The task is simplifying or factoring a scalar expression — use expression_solver instead.\n"
            "  - The task is probability or descriptive statistics — use stats_solver instead.\n"
            "\n"
            "INPUT RULES:\n"
            "  - task MUST be one of: determinant, trace, rank, inverse, rref, eigenvalues, linear_solve, multiply, svd, qr, null_space, matrix_power.\n"
            "  - matrix is required for every task. Entries may be integers, numbers, or numeric strings.\n"
            "  - For linear_solve pass vector (right-hand side b). For multiply pass matrix_b.\n"
            "  - For matrix_power pass power (integer exponent, may be negative for inverse power).\n"
            "\n"
            "OUTPUT RULES:\n"
            "  - For determinant/trace/rank returns a scalar or integer.\n"
            "  - For inverse/rref/eigenvalues/svd/qr/null_space returns the corresponding matrix or vector decomposition.\n"
            "  - For linear_solve returns the solution vector x.\n"
            "  - For multiply returns the product matrix. For matrix_power returns the powered matrix.\n"
            "  - Singular or non-invertible matrices return a clear error rather than a degraded result."
        ), parameters_schema=PARAMETERS_SCHEMA, solver=LinearAlgebraSolver())
