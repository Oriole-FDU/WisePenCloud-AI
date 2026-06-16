from __future__ import annotations

from typing import Any

from chat.application.tools.math_tools.base_tool import MathSolverTool
from chat.application.tools.math_tools.services.solvers.calculus import CalculusSolver
from chat.application.tools.math_tools.services.tasks import CalculusTask

PARAMETERS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "task": {
            "type": "string",
            "enum": CalculusTask.values(),
            "description": "Calculus task to execute.",
        },
        "expression": {
            "type": "string",
            "minLength": 1,
            "description": "Math expression for differentiation, integration, limits, series, sums, or transforms.",
        },
        "equation": {
            "type": "string",
            "minLength": 1,
            "description": "ODE equation for solve_ode, for example Derivative(y(x), x) - y(x) = 0.",
        },
        "variable": {
            "type": "string",
            "minLength": 1,
            "description": "Main variable. Defaults to x, or t for laplace_transform when omitted.",
        },
        "variables": {
            "type": "array",
            "items": {"type": "string", "minLength": 1},
            "minItems": 1,
            "description": "Allowed variable names used by the expression parser.",
        },
        "variable2": {
            "type": "string",
            "minLength": 1,
            "description": "Second variable for double_integral. Defaults to y.",
        },
        "function": {
            "type": "string",
            "minLength": 1,
            "description": "Dependent function name for solve_ode. Defaults to y.",
        },
        "transform_variable": {
            "type": "string",
            "minLength": 1,
            "description": "Laplace-domain variable. Defaults to s.",
        },
        "point": {
            "type": "string",
            "description": "Limit or Taylor expansion point.",
        },
        "order": {
            "type": "integer",
            "description": "Taylor series order. Defaults to 6.",
        },
        "lower_bound": {
            "type": "string",
            "description": "Lower bound for definite_integral or first variable of double_integral.",
        },
        "upper_bound": {
            "type": "string",
            "description": "Upper bound for definite_integral or first variable of double_integral.",
        },
        "lower": {
            "type": "string",
            "description": "Alias for lower_bound, or lower index for summation.",
        },
        "upper": {
            "type": "string",
            "description": "Alias for upper_bound, or upper index for summation.",
        },
        "lower2": {
            "type": "string",
            "description": "Lower bound for variable2 in double_integral.",
        },
        "upper2": {
            "type": "string",
            "description": "Upper bound for variable2 in double_integral.",
        },
    },
    "required": ["task"],
    "additionalProperties": False,
}


class CalculusSolverTool(MathSolverTool):
    """微积分求解工具门面。"""

    def __init__(self) -> None:
        super().__init__(name="calculus_solver", description=(
            "Solve deterministic calculus tasks with SymPy: differentiation, partial differentiation, indefinite/definite integrals, double integrals, limits, Taylor series, summations, ODEs, and Laplace transforms.\n"
            "\n"
            "WHEN TO TRIGGER:\n"
            "  - MUST trigger when the user asks to differentiate or integrate an expression.\n"
            "  - MUST trigger when computing a limit, Taylor series, or summation.\n"
            "  - MUST trigger when solving an ordinary differential equation (ODE).\n"
            "  - MUST trigger when computing a Laplace transform or double integral.\n"
            "DO NOT TRIGGER when:\n"
            "  - The task is solving an algebraic equation, inequality, or numeric root — use equation_solver instead.\n"
            "  - The task is matrix determinant, inverse, eigenvalues, or linear systems Ax=b — use linear_algebra_solver instead.\n"
            "  - The task is simplifying, expanding, factoring, or numerically evaluating an expression — use expression_solver instead.\n"
            "  - The task is probability or descriptive statistics — use stats_solver instead.\n"
            "\n"
            "INPUT RULES:\n"
            "  - task MUST be one of: differentiate, partial_differentiate, indefinite_integral, definite_integral, double_integral, limit, taylor_series, summation, solve_ode, laplace_transform.\n"
            "  - expression is required for every task except solve_ode (which uses equation).\n"
            "  - variable defaults to x (or t for laplace_transform). Use variables to whitelist parser symbols.\n"
            "  - For definite_integral pass lower_bound and upper_bound. For double_integral also pass lower2/upper2 and variable2 (default y).\n"
            "  - For limit and taylor_series pass point (default 0). For taylor_series pass order (default 6).\n"
            "  - For solve_ode pass equation and function (default y).\n"
            "  - For laplace_transform pass transform_variable (default s).\n"
            "\n"
            "OUTPUT RULES:\n"
            "  - Returns exact symbolic results; numeric approximations are not produced here.\n"
            "  - For definite_integral and summation, returns the evaluated closed form when available.\n"
            "  - For solve_ode, returns the general solution (with integration constants).\n"
            "  - This tool never executes arbitrary Python code."
        ), parameters_schema=PARAMETERS_SCHEMA, solver=CalculusSolver())
