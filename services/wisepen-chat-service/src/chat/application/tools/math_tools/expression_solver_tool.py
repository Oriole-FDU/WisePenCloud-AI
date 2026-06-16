from __future__ import annotations

from typing import Any

from chat.application.tools.math_tools.base_tool import MathSolverTool
from chat.application.tools.math_tools.services.solvers.expression import ExpressionSolver
from chat.application.tools.math_tools.services.tasks import ExpressionTask

PARAMETERS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "task": {
            "type": "string",
            "enum": ExpressionTask.values(),
            "description": "Expression, combinatorics, or number theory task to execute.",
        },
        "expression": {
            "type": "string",
            "minLength": 1,
            "description": "Expression for simplify, expand, factor, or numeric.",
        },
        "variables": {
            "type": "array",
            "items": {"type": "string", "minLength": 1},
            "minItems": 1,
            "description": "Allowed variable names used by the expression parser.",
        },
        "n": {
            "type": "integer",
            "description": "Integer n for factorial, combinations, and permutations.",
        },
        "k": {
            "type": "integer",
            "description": "Integer k for combinations and permutations.",
        },
        "integers": {
            "type": "array",
            "items": {"type": "integer"},
            "minItems": 1,
            "description": "Integer array for gcd or lcm.",
        },
        "integer": {
            "type": "integer",
            "description": "Integer to factor for prime_factors.",
        },
    },
    "required": ["task"],
    "additionalProperties": False,
}


class ExpressionSolverTool(MathSolverTool):
    """表达式、组合和轻量数论工具门面。"""

    def __init__(self) -> None:
        super().__init__(name="expression_solver", description=(
            "Simplify, expand, factor, and numerically evaluate expressions; compute basic combinatorics (factorial, combinations, permutations) and lightweight number theory (gcd, lcm, prime factors).\n"
            "\n"
            "WHEN TO TRIGGER:\n"
            "  - MUST trigger when the user asks to simplify, expand, factor, or numerically evaluate a symbolic expression.\n"
            "  - MUST trigger when computing factorial, combinations (nCr), or permutations (nPr).\n"
            "  - MUST trigger when computing gcd, lcm, or prime factorization of integers.\n"
            "DO NOT TRIGGER when:\n"
            "  - The task is solving an equation, inequality, or optimization — use equation_solver instead.\n"
            "  - The task is differentiation, integration, limits, or ODEs — use calculus_solver instead.\n"
            "  - The task is matrix operations or linear systems — use linear_algebra_solver instead.\n"
            "  - The task is probability distributions or descriptive statistics — use stats_solver instead.\n"
            "\n"
            "INPUT RULES:\n"
            "  - task MUST be one of: simplify, expand, factor, numeric, factorial, combinations, permutations, gcd, lcm, prime_factors.\n"
            "  - For simplify/expand/factor/numeric pass expression and (optionally) variables to whitelist parser symbols.\n"
            "  - For factorial pass n. For combinations/permutations pass n and k.\n"
            "  - For gcd/lcm pass integers array (min length 1).\n"
            "  - For prime_factors pass integer.\n"
            "\n"
            "OUTPUT RULES:\n"
            "  - For simplify/expand/factor returns the exact symbolic result.\n"
            "  - For numeric returns a floating-point approximation.\n"
            "  - For combinatorics and number theory returns integer results.\n"
            "  - For prime_factors returns the list of prime factors (with multiplicity)."
        ), parameters_schema=PARAMETERS_SCHEMA, solver=ExpressionSolver())
