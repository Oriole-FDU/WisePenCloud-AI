from __future__ import annotations

import asyncio
from typing import Any

import numpy as np
import sympy as sp
from scipy import optimize

from chat.application.tools.math_tools.services.errors import MathSolverError
from chat.application.tools.math_tools.services.solvers.utils.expression_parser import MathExpressionParser
from chat.application.tools.math_tools.services.solvers.utils.reader import (
    read_latex,
    read_numeric_values,
    read_variable_name,
    read_variable_names,
)
from chat.application.tools.math_tools.services.tasks import EquationTask


class EquationSolver:
    """方程、不等式和轻量优化计算。"""

    async def solve(self, task: str, payload: dict[str, Any]) -> Any:
        return await asyncio.to_thread(self._solve_sync, task, payload)

    def _solve_sync(self, task: str, payload: dict[str, Any]) -> Any:
        task_type = EquationTask(task)
        numeric: Any = None

        # 根据任务类型进行路由分发
        match task_type:
            case EquationTask.SOLVE_EQUATION:
                exact = self._solve_equation(payload)

            case EquationTask.SOLVE_SYSTEM:
                exact = self._solve_system(payload)

            case EquationTask.SOLVE_INEQUALITY:
                exact = self._solve_inequality(payload)

            case EquationTask.NUMERIC_ROOT:
                exact, numeric = self._numeric_root(payload)

            case EquationTask.NUMERIC_MINIMIZE:
                exact, numeric = self._numeric_minimize(payload)

            case EquationTask.CONSTRAINED_MINIMIZE:
                exact, numeric = self._constrained_minimize(payload)

            case _:
                raise MathSolverError(f"unsupported equation task: {task_type.value}")

        return numeric if numeric is not None else read_latex(exact)

    @staticmethod
    def _solve_equation(payload: dict[str, Any]) -> Any:
        var_name = read_variable_name(payload)
        equation = MathExpressionParser.parse_equation(
            payload.get("equation") or payload.get("expression"),
            [var_name],
        )
        return sp.solve(equation, sp.Symbol(var_name))

    @staticmethod
    def _solve_system(payload: dict[str, Any]) -> Any:
        names = read_variable_names(payload)
        equations = payload["equations"]

        return sp.solve(
            [MathExpressionParser.parse_equation(eq, names) for eq in equations],
            [sp.Symbol(name) for name in names],
            dict=True,
        )

    @staticmethod
    def _solve_inequality(payload: dict[str, Any]) -> Any:
        var_name = read_variable_name(payload)
        expr = payload.get("inequality") or payload.get("expression")

        return sp.solve_univariate_inequality(
            MathExpressionParser.parse_inequality(expr, var_name),
            sp.Symbol(var_name),
        )

    @staticmethod
    def _numeric_root(payload: dict[str, Any]) -> tuple[Any, Any]:
        var_name = read_variable_name(payload)
        variable = sp.Symbol(var_name)
        expression = MathExpressionParser.parse_expr(payload.get("expression"), [var_name])
        func = sp.lambdify(variable, expression, modules=["numpy"])

        # 分支 1: 区间求根 (Bracketed Root Search)
        if payload.get("lower") is not None and payload.get("upper") is not None:
            root = optimize.root_scalar(
                func,
                bracket=[
                    float(MathExpressionParser.parse_bound(payload.get("lower"), "lower", [var_name])),
                    float(MathExpressionParser.parse_bound(payload.get("upper"), "upper", [var_name])),
                ],
            )
            if not root.converged:
                raise MathSolverError("numeric root search did not converge.")
            return root.root, root.root

        # 分支 2: 单点迭代求根 (Point Estimation)
        point = float(MathExpressionParser.parse_bound(payload.get("point"), "point", [var_name]))
        root = optimize.root(lambda values: [func(values[0])], [point])
        if not root.success:
            raise MathSolverError("numeric root search did not converge.")

        return float(root.x[0]), float(root.x[0])

    @staticmethod
    def _numeric_minimize(payload: dict[str, Any]) -> tuple[Any, Any]:
        var_name = read_variable_name(payload)
        variable = sp.Symbol(var_name)
        expression = MathExpressionParser.parse_expr(payload.get("expression"), [var_name])
        func = sp.lambdify(variable, expression, modules=["numpy"])

        lower = float(MathExpressionParser.parse_bound(payload.get("lower"), "lower", [var_name]))
        upper = float(MathExpressionParser.parse_bound(payload.get("upper"), "upper", [var_name]))

        result = optimize.minimize_scalar(func, bounds=(lower, upper), method="bounded")
        if not result.success:
            raise MathSolverError("numeric minimization did not converge.")

        exact = {"x": float(result.x), "fun": float(result.fun)}
        return exact, exact

    @staticmethod
    def _constrained_minimize(payload: dict[str, Any]) -> tuple[Any, Any]:
        names = read_variable_names(payload, default=("x", "y"))
        symbols = [sp.Symbol(name) for name in names]
        expression = MathExpressionParser.parse_expr(payload.get("expression"), names)
        func = sp.lambdify(symbols, expression, modules=["numpy"])

        initial = read_numeric_values(payload["initial_guess"], name="initial_guess")
        if initial.size != len(names):
            raise MathSolverError("initial_guess length must match variables.")

        bounds = OptimizationPayloadAdapter.bounds(payload, len(names))
        constraints = [
            {
                "type": "ineq",
                "fun": OptimizationPayloadAdapter.constraint_function(raw, names, symbols)
            }
            for raw in (payload.get("constraints") or [])
        ]

        result = optimize.minimize(
            lambda values: float(func(*values)),
            x0=initial,
            bounds=bounds,
            constraints=constraints,
        )
        if not result.success:
            raise MathSolverError(f"constrained minimization did not converge: {result.message}")

        exact = {
            "x": np.asarray(result.x, dtype=float).tolist(),
            "fun": float(result.fun)
        }
        return exact, exact


class OptimizationPayloadAdapter:
    """优化任务的 bounds 与 constraints 入参适配命名空间。"""

    @staticmethod
    def bounds(payload: dict[str, Any], size: int) -> list[tuple[float | None, float | None]] | None:
        """读取 scipy.optimize.minimize 使用的 bounds。"""
        lower_bounds = payload.get("lower_bounds")
        upper_bounds = payload.get("upper_bounds")

        if lower_bounds is None and upper_bounds is None:
            return None

        lower = [None] * size if lower_bounds is None else list(lower_bounds)
        upper = [None] * size if upper_bounds is None else list(upper_bounds)

        if len(lower) != size or len(upper) != size:
            raise MathSolverError("bounds length must match variables.")

        return [
            (
                None if low is None else float(low),
                None if high is None else float(high),
            )
            for low, high in zip(lower, upper, strict=True)
        ]

    @staticmethod
    def constraint_function(raw: str, names: list[str], symbols: list[sp.Symbol]) -> Any:
        """把 `>= 0` 约束表达式转为 SciPy 约束函数。"""
        if not raw.strip():
            raise MathSolverError("constraints must contain non-empty expressions interpreted as >= 0.")

        expression = MathExpressionParser.parse_expr(raw, names)
        func = sp.lambdify(symbols, expression, modules=["numpy"])
        return lambda values: float(func(*values))

    @staticmethod
    def numeric_values(value: object, *, name: str) -> np.ndarray:
        """将一维数值序列解析为 NumPy array。"""
        return read_numeric_values(value, name=name)