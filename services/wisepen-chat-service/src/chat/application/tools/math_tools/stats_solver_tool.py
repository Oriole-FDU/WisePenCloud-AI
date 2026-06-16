from __future__ import annotations

from typing import Any

from chat.application.tools.math_tools.base_tool import MathSolverTool
from chat.application.tools.math_tools.services.solvers.stats import StatsSolver
from chat.application.tools.math_tools.services.tasks import StatsTask

PARAMETERS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "task": {
            "type": "string",
            "enum": StatsTask.values(),
            "description": "Statistics or probability task to execute.",
        },
        "n": {
            "type": "integer",
            "description": "Trial count for binomial_prob.",
        },
        "k": {
            "type": "integer",
            "description": "Event count for binomial_prob or poisson_prob.",
        },
        "probability": {
            "type": "string",
            "minLength": 1,
            "description": "Success probability expression for binomial_prob.",
        },
        "rate": {
            "type": "number",
            "description": "Poisson rate parameter.",
        },
        "point": {
            "type": "number",
            "description": "CDF evaluation point.",
        },
        "mean": {
            "type": "number",
            "description": "Normal distribution mean. Defaults to 0.",
        },
        "std": {
            "type": "number",
            "description": "Normal distribution standard deviation. Defaults to 1.",
        },
        "df": {
            "type": "number",
            "description": "Degrees of freedom for t_cdf or chi2_cdf.",
        },
        "dfn": {
            "type": "number",
            "description": "Numerator degrees of freedom for f_cdf.",
        },
        "dfd": {
            "type": "number",
            "description": "Denominator degrees of freedom for f_cdf.",
        },
        "expression": {
            "type": "string",
            "minLength": 1,
            "description": "Expression over variable for uniform_expectation_variance.",
        },
        "variable": {
            "type": "string",
            "minLength": 1,
            "description": "Variable for uniform_expectation_variance. Defaults to x.",
        },
        "lower": {
            "type": "string",
            "description": "Lower integer support bound for uniform_expectation_variance.",
        },
        "upper": {
            "type": "string",
            "description": "Upper integer support bound for uniform_expectation_variance.",
        },
        "values": {
            "type": "array",
            "items": {"type": "number"},
            "minItems": 1,
            "description": "Numeric sample values for descriptive_stats.",
        },
        "x_values": {
            "type": "array",
            "items": {"type": "number"},
            "minItems": 1,
            "description": "X values for linear_regression or correlation.",
        },
        "y_values": {
            "type": "array",
            "items": {"type": "number"},
            "minItems": 1,
            "description": "Y values for linear_regression or correlation.",
        },
        "method": {
            "type": "string",
            "enum": ["pearson", "spearman"],
            "description": "Correlation method. Defaults to pearson.",
        },
    },
    "required": ["task"],
    "additionalProperties": False,
}


class StatsSolverTool(MathSolverTool):
    """统计和概率求解工具门面。"""

    def __init__(self) -> None:
        super().__init__(name="stats_solver", description=(
            "Compute probability and statistics tasks: binomial and Poisson probabilities, normal/t/chi-square/F CDFs, finite uniform expectation and variance, descriptive statistics, linear regression, and Pearson/Spearman correlation.\n"
            "\n"
            "WHEN TO TRIGGER:\n"
            "  - MUST trigger when computing binomial or Poisson probabilities.\n"
            "  - MUST trigger when evaluating normal, t, chi-square, or F distribution CDFs.\n"
            "  - MUST trigger when computing expectation/variance of a finite uniform discrete variable.\n"
            "  - MUST trigger when computing descriptive statistics (mean, variance, median, quartiles) of a sample.\n"
            "  - MUST trigger when fitting a linear regression or computing Pearson/Spearman correlation.\n"
            "DO NOT TRIGGER when:\n"
            "  - The task is solving an equation or optimization — use equation_solver instead.\n"
            "  - The task is differentiation, integration, or ODEs — use calculus_solver instead.\n"
            "  - The task is matrix operations — use linear_algebra_solver instead.\n"
            "  - The task is simplifying or factoring an expression — use expression_solver instead.\n"
            "\n"
            "INPUT RULES:\n"
            "  - task MUST be one of: binomial_prob, poisson_prob, normal_cdf, t_cdf, chi2_cdf, f_cdf, uniform_expectation_variance, descriptive_stats, linear_regression, correlation.\n"
            "  - For binomial_prob pass n, k, probability. For poisson_prob pass rate and k.\n"
            "  - For normal_cdf pass point, mean (default 0), std (default 1).\n"
            "  - For t_cdf/chi2_cdf pass point and df. For f_cdf pass point, dfn, dfd.\n"
            "  - For uniform_expectation_variance pass expression, variable (default x), lower, upper.\n"
            "  - For descriptive_stats pass values array. For linear_regression pass x_values and y_values.\n"
            "  - For correlation pass x_values, y_values, and method (pearson or spearman, default pearson).\n"
            "\n"
            "OUTPUT RULES:\n"
            "  - For probability/CDF tasks returns a numeric probability in [0, 1].\n"
            "  - For uniform_expectation_variance returns expectation and variance as exact or numeric values.\n"
            "  - For descriptive_stats returns mean, variance, standard deviation, median, and quartiles.\n"
            "  - For linear_regression returns slope, intercept, and R-squared.\n"
            "  - For correlation returns the correlation coefficient in [-1, 1]."
        ), parameters_schema=PARAMETERS_SCHEMA, solver=StatsSolver())
