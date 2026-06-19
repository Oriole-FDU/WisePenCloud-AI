from __future__ import annotations

from typing import Any

from chat.application.tools.math_tools.base_tool import MathSolverTool
from chat.application.tools.math_tools.services.solvers.equation import EquationSolver
from chat.application.tools.math_tools.services.tasks import EquationTask

PARAMETERS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "task": {
            "type": "string",
            "enum": EquationTask.values(),
            "description": "Equation, inequality, root finding, or optimization task to execute.",
        },
        "expression": {
            "type": "string",
            "minLength": 1,
            "description": "Expression for numeric_root, solve_inequality, numeric_minimize, or constrained_minimize.",
        },
        "equation": {
            "type": "string",
            "minLength": 1,
            "description": "Single equation for solve_equation, for example x^2 - 4 = 0.",
        },
        "equations": {
            "type": "array",
            "items": {"type": "string", "minLength": 1},
            "minItems": 1,
            "description": "Equation array for solve_system.",
        },
        "inequality": {
            "type": "string",
            "minLength": 1,
            "description": "Relational expression for solve_inequality, for example x^2 < 4.",
        },
        "variable": {
            "type": "string",
            "minLength": 1,
            "description": "Single variable name. Defaults to x.",
        },
        "variables": {
            "type": "array",
            "items": {"type": "string", "minLength": 1},
            "minItems": 1,
            "description": "Variable names for systems and constrained optimization.",
        },
        "point": {
            "type": "string",
            "description": "Initial point for numeric_root when no bracket is supplied.",
        },
        "lower": {
            "type": "string",
            "description": "Lower bracket or bound for numeric_root and numeric_minimize.",
        },
        "upper": {
            "type": "string",
            "description": "Upper bracket or bound for numeric_root and numeric_minimize.",
        },
        "initial_guess": {
            "type": "array",
            "items": {"type": "number"},
            "minItems": 1,
            "description": "Initial numeric vector for constrained_minimize.",
        },
        "lower_bounds": {
            "type": "array",
            "items": {"type": "number"},
            "description": "Optional lower bounds for constrained_minimize variables.",
        },
        "upper_bounds": {
            "type": "array",
            "items": {"type": "number"},
            "description": "Optional upper bounds for constrained_minimize variables.",
        },
        "constraints": {
            "type": "array",
            "items": {"type": "string", "minLength": 1},
            "description": "Inequality constraint expressions interpreted as >= 0.",
        },
    },
    "required": ["task"],
    "additionalProperties": False,
}


class EquationSolverTool(MathSolverTool):
    """方程、不等式和轻量优化工具门面。"""

    def __init__(self) -> None:
        super().__init__(name="equation_solver", description=(
            "Solve algebraic equations, equation systems, univariate inequalities, numeric roots, and bounded/constrained minimization.\n"
            "\n"
            "WHEN TO TRIGGER:\n"
            "  - MUST trigger when the user asks to solve an equation, equation system, or univariate inequality.\n"
            "  - MUST trigger when finding a numeric root of f(x)=0 with a bracket or initial guess.\n"
            "  - SHOULD trigger when minimizing a scalar or constrained objective within bounds.\n"
            "DO NOT TRIGGER when:\n"
            "  - The task is differentiation, integration, limits, series, or ODEs — use calculus_solver instead.\n"
            "  - The task is matrix determinant, inverse, eigenvalues, or linear systems Ax=b — use linear_algebra_solver instead.\n"
            "  - The task is simplifying, expanding, factoring, or numerically evaluating an expression — use expression_solver instead.\n"
            "\n"
            "INPUT RULES:\n"
            "  - task MUST be one of: solve_equation, solve_system, solve_inequality, numeric_root, numeric_minimize, constrained_minimize.\n"
            "  - For solve_equation pass equation and variable (default x).\n"
            "  - For solve_system pass equations array and variables array.\n"
            "  - For solve_inequality pass inequality and variable.\n"
            "  - For numeric_root pass expression plus either point or lower/upper bracket.\n"
            "  - For numeric_minimize pass expression with lower/upper bounds.\n"
            "  - For constrained_minimize pass expression, variables, initial_guess, and optional lower_bounds/upper_bounds/constraints (>=0).\n"
            "\n"
            "OUTPUT RULES:\n"
            "  - Returns exact symbolic solutions when closed-form exists, otherwise numeric approximations.\n"
            "  - For systems, returns a mapping of variable to value.\n"
            "  - For minimization, returns the minimizer and the objective value."
        ), parameters_schema=PARAMETERS_SCHEMA, solver=EquationSolver())
