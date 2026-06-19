from __future__ import annotations

import asyncio
import math
from typing import Any

import sympy as sp

from chat.application.tools.math_tools.services.errors import MathSolverError
from chat.application.tools.math_tools.services.solvers.utils.expression_parser import MathExpressionParser
from chat.application.tools.math_tools.services.solvers.utils.reader import (
    read_latex,
    read_variable_names,
)
from chat.application.tools.math_tools.services.tasks import ExpressionTask


class ExpressionSolver:
    """基础符号、组合和轻量数论计算。"""

    async def solve(self, task: str, payload: dict[str, Any]) -> Any:
        return await asyncio.to_thread(self._solve_sync, task, payload)

    def _solve_sync(self, task: str, payload: dict[str, Any]) -> Any:
        task_type = ExpressionTask(task)

        exact: Any
        numeric: Any = None

        # 根据任务类型进行路由分发
        match task_type:
            # 1. 符号代数类任务 (需要解析表达式)
            case ExpressionTask.SIMPLIFY | ExpressionTask.EXPAND | ExpressionTask.FACTOR | ExpressionTask.NUMERIC:
                expression = MathExpressionParser.parse_expr(
                    payload.get("expression"),
                    read_variable_names(payload),
                )

                if task_type is ExpressionTask.SIMPLIFY:
                    exact = sp.simplify(expression)
                elif task_type is ExpressionTask.EXPAND:
                    exact = sp.expand(expression)
                elif task_type is ExpressionTask.FACTOR:
                    exact = sp.factor(expression)
                else:
                    exact = sp.N(expression)
                    try:
                        numeric = float(exact)
                    except (TypeError, ValueError):
                        numeric = None

            # 2. 组合数学类任务
            case ExpressionTask.FACTORIAL:
                exact = sp.factorial(payload["n"])

            case ExpressionTask.COMBINATIONS:
                exact = sp.binomial(payload["n"], payload["k"])

            case ExpressionTask.PERMUTATIONS:
                n = payload["n"]
                k = payload["k"]
                exact = sp.factorial(n) / sp.factorial(n - k)

            # 3. 数论类任务
            case ExpressionTask.GCD | ExpressionTask.LCM:
                integers = payload["integers"]
                exact = math.gcd(*integers) if task_type is ExpressionTask.GCD else math.lcm(*integers)

            case ExpressionTask.PRIME_FACTORS:
                exact = sp.factorint(payload["integer"])

            case _:
                raise MathSolverError(f"unsupported expression task: {task_type.value}")

        return numeric if numeric is not None else read_latex(exact)