from __future__ import annotations

import asyncio
import math
from typing import Any

import sympy as sp

from chat.application.tools.math_tools.services.errors import MathSolverError
from chat.application.tools.math_tools.services.solvers.utils.expression_parser import MathExpressionParser
from chat.application.tools.math_tools.services.solvers.utils.latex import latex_or_none
from chat.application.tools.math_tools.services.solvers.utils.payload_reader import MathPayloadReader
from chat.application.tools.math_tools.services.tasks import ExpressionTask


class ExpressionSolver:
    """基础符号、组合和轻量数论计算。"""

    async def solve(self, task: str, payload: dict[str, Any]) -> Any:
        return await asyncio.to_thread(self._solve_sync, task, payload)

    def _solve_sync(self, task: str, payload: dict[str, Any]) -> Any:
        task_type = ExpressionTask(task)

        exact: Any
        numeric: Any = None
        if task_type in {
            ExpressionTask.SIMPLIFY,
            ExpressionTask.EXPAND,
            ExpressionTask.FACTOR,
            ExpressionTask.NUMERIC,
        }:
            expression = MathExpressionParser.parse_expr(
                payload.get("expression"),
                MathPayloadReader.variable_names(payload),
            )
            if task_type is ExpressionTask.SIMPLIFY:
                exact = sp.simplify(expression)
            elif task_type is ExpressionTask.EXPAND:
                exact = sp.expand(expression)
            elif task_type is ExpressionTask.FACTOR:
                exact = sp.factor(expression)
            else:
                exact = sp.N(expression)
                numeric = self._float_or_none(exact)
        elif task_type is ExpressionTask.FACTORIAL:
            exact = sp.factorial(payload["n"])
        elif task_type is ExpressionTask.COMBINATIONS:
            exact = sp.binomial(payload["n"], payload["k"])
        elif task_type is ExpressionTask.PERMUTATIONS:
            n = payload["n"]
            k = payload["k"]
            exact = sp.factorial(n) / sp.factorial(n - k)
        elif task_type in {ExpressionTask.GCD, ExpressionTask.LCM}:
            integers = payload["integers"]
            exact = math.gcd(*integers) if task_type is ExpressionTask.GCD else math.lcm(*integers)
        elif task_type is ExpressionTask.PRIME_FACTORS:
            exact = sp.factorint(payload["integer"])
        else:
            raise MathSolverError(f"unsupported expression task: {task_type.value}")

        return numeric if numeric is not None else latex_or_none(exact)

    @staticmethod
    def _float_or_none(value: Any) -> float | None:
        """尽力把数值结果转换为 float。"""
        try:
            return float(value)
        except (TypeError, ValueError):
            return None