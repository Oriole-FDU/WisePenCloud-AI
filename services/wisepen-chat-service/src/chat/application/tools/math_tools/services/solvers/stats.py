from __future__ import annotations

import asyncio
from typing import Any

import numpy as np
import sympy as sp
from scipy import stats

from chat.application.tools.math_tools.services.errors import MathSolverError
from chat.application.tools.math_tools.services.solvers.utils.expression_parser import MathExpressionParser
from chat.application.tools.math_tools.services.solvers.utils.latex import latex_or_none
from chat.application.tools.math_tools.services.solvers.utils.payload_reader import MathPayloadReader
from chat.application.tools.math_tools.services.tasks import StatsTask


class StatsSolver:
    """统计、概率和基础回归计算。"""

    async def solve(self, task: str, payload: dict[str, Any]) -> Any:
        return await asyncio.to_thread(self._solve_sync, task, payload)

    def _solve_sync(self, task: str, payload: dict[str, Any]) -> Any:
        task_type = StatsTask(task)

        exact: Any = None
        numeric: Any = None
        if task_type is StatsTask.BINOMIAL_PROB:
            probability = MathExpressionParser.parse_expr(payload.get("probability"), [])
            n = payload["n"]
            k = payload["k"]
            exact = sp.binomial(n, k) * probability**k * (1 - probability) ** (n - k)
        elif task_type is StatsTask.POISSON_PROB:
            numeric = float(
                stats.poisson.pmf(
                    payload["k"],
                    float(payload["rate"]),
                )
            )
            exact = numeric
        elif task_type is StatsTask.NORMAL_CDF:
            numeric = float(
                stats.norm.cdf(
                    float(payload["point"]),
                    loc=float(payload.get("mean") or 0),
                    scale=float(payload.get("std") or 1),
                )
            )
            exact = numeric
        elif task_type is StatsTask.UNIFORM_EXPECTATION_VARIANCE:
            exact = self._uniform_expectation_variance(payload)
            numeric = {key: float(value) for key, value in exact.items()}
        elif task_type is StatsTask.DESCRIPTIVE_STATS:
            exact = self._descriptive_stats(payload)
            numeric = exact
        elif task_type is StatsTask.T_CDF:
            numeric = float(
                stats.t.cdf(
                    float(payload["point"]),
                    df=float(payload["df"]),
                )
            )
            exact = numeric
        elif task_type is StatsTask.CHI2_CDF:
            numeric = float(
                stats.chi2.cdf(
                    float(payload["point"]),
                    df=float(payload["df"]),
                )
            )
            exact = numeric
        elif task_type is StatsTask.F_CDF:
            numeric = float(
                stats.f.cdf(
                    float(payload["point"]),
                    dfn=float(payload["dfn"]),
                    dfd=float(payload["dfd"]),
                )
            )
            exact = numeric
        elif task_type is StatsTask.LINEAR_REGRESSION:
            exact = self._linear_regression(payload)
            numeric = exact
        elif task_type is StatsTask.CORRELATION:
            exact = self._correlation(payload)
            numeric = exact
        else:
            raise MathSolverError(f"unsupported stats task: {task_type.value}")

        return numeric if numeric is not None else latex_or_none(exact)

    @staticmethod
    def _uniform_expectation_variance(payload: dict[str, Any]) -> dict[str, Any]:
        var_name = MathPayloadReader.variable_name(payload)
        variable = sp.Symbol(var_name)
        lower = int(MathExpressionParser.parse_bound(payload.get("lower"), "lower", [var_name]))
        upper = int(MathExpressionParser.parse_bound(payload.get("upper"), "upper", [var_name]))
        expression = MathExpressionParser.parse_expr(payload.get("expression"), [var_name])
        count = upper - lower + 1
        if count <= 0:
            raise MathSolverError("upper must be greater than or equal to lower.")
        mean = sp.simplify(sp.summation(expression, (variable, lower, upper)) / count)
        second = sp.simplify(sp.summation(expression ** 2, (variable, lower, upper)) / count)
        return {"expectation": mean, "variance": sp.simplify(second - mean ** 2)}

    @staticmethod
    def _descriptive_stats(payload: dict[str, Any]) -> dict[str, float]:
        values = MathPayloadReader.numeric_values(payload.get("values"), name="values")
        q1, q3 = np.percentile(values, [25, 75])
        return {
            "count": float(values.size),
            "mean": float(np.mean(values)),
            "variance": float(np.var(values, ddof=1)) if values.size > 1 else 0.0,
            "std": float(np.std(values, ddof=1)) if values.size > 1 else 0.0,
            "median": float(np.median(values)),
            "min": float(np.min(values)),
            "max": float(np.max(values)),
            "q1": float(q1),
            "q3": float(q3),
            "iqr": float(q3 - q1),
        }

    @staticmethod
    def _linear_regression(payload: dict[str, Any]) -> dict[str, float]:
        x_values = MathPayloadReader.numeric_values(payload.get("x_values"), name="x_values")
        y_values = MathPayloadReader.numeric_values(payload.get("y_values"), name="y_values")
        if x_values.size != y_values.size:
            raise MathSolverError("x_values and y_values must have the same length.")
        result = stats.linregress(x_values, y_values)
        return {
            "slope": float(result.slope),
            "intercept": float(result.intercept),
            "rvalue": float(result.rvalue),
            "pvalue": float(result.pvalue),
            "stderr": float(result.stderr),
            "intercept_stderr": float(result.intercept_stderr),
        }

    @staticmethod
    def _correlation(payload: dict[str, Any]) -> dict[str, float | str]:
        x_values = MathPayloadReader.numeric_values(payload.get("x_values"), name="x_values")
        y_values = MathPayloadReader.numeric_values(payload.get("y_values"), name="y_values")
        if x_values.size != y_values.size:
            raise MathSolverError("x_values and y_values must have the same length.")
        method = str(payload.get("method") or "pearson")
        if method == "pearson":
            result = stats.pearsonr(x_values, y_values)
        else:
            result = stats.spearmanr(x_values, y_values)
        return {
            "method": method,
            "statistic": float(result.statistic),
            "pvalue": float(result.pvalue),
        }