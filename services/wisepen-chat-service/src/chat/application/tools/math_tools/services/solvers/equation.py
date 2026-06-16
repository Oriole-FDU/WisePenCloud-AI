from __future__ import annotations

import asyncio
from typing import Any

import numpy as np
import sympy as sp
from scipy import optimize

from chat.application.tools.math_tools.services.errors import MathSolverError
from chat.application.tools.math_tools.services.solvers.utils.expression_parser import MathExpressionParser
from chat.application.tools.math_tools.services.solvers.utils.latex import latex_or_none
from chat.application.tools.math_tools.services.solvers.utils.payload_reader import MathPayloadReader
from chat.application.tools.math_tools.services.tasks import EquationTask


class EquationSolver:
    """方程、不等式和轻量优化计算。"""

    async def solve(self, task: str, payload: dict[str, Any]) -> Any:
        return await asyncio.to_thread(self._solve_sync, task, payload)

    def _solve_sync(self, task: str, payload: dict[str, Any]) -> Any:
        task_type = EquationTask(task)

        numeric: Any = None
        if task_type is EquationTask.SOLVE_EQUATION:
            exact = self._solve_equation(payload)
        elif task_type is EquationTask.SOLVE_SYSTEM:
            exact = self._solve_system(payload)
        elif task_type is EquationTask.SOLVE_INEQUALITY:
            exact = self._solve_inequality(payload)
        elif task_type is EquationTask.NUMERIC_ROOT:
            exact, numeric = self._numeric_root(payload)
        elif task_type is EquationTask.NUMERIC_MINIMIZE:
            exact, numeric = self._numeric_minimize(payload)
        elif task_type is EquationTask.CONSTRAINED_MINIMIZE:
            exact, numeric = self._constrained_minimize(payload)
        else:
            raise MathSolverError(f"unsupported equation task: {task_type.value}")

        return numeric if numeric is not None else latex_or_none(exact)

    @staticmethod
    def _solve_equation(payload: dict[str, Any]) -> Any:
        var_name = MathPayloadReader.variable_name(payload)
        equation = MathExpressionParser.parse_equation(
            payload.get("equation") or payload.get("expression"),
            [var_name],
        )
        return sp.solve(equation, sp.Symbol(var_name))

    @staticmethod
    def _solve_system(payload: dict[str, Any]) -> Any:
        names = MathPayloadReader.variable_names(payload)
        equations = payload["equations"]
        return sp.solve(
            [MathExpressionParser.parse_equation(equation, names) for equation in equations],
            [sp.Symbol(name) for name in names],
            dict=True,
        )

    @staticmethod
    def _solve_inequality(payload: dict[str, Any]) -> Any:
        var_name = MathPayloadReader.variable_name(payload)
        return sp.solve_univariate_inequality(
            MathExpressionParser.parse_inequality(payload.get("inequality") or payload.get("expression"), var_name),
            sp.Symbol(var_name),
        )

    @staticmethod
    def _numeric_root(payload: dict[str, Any]) -> tuple[Any, Any]:
        var_name = MathPayloadReader.variable_name(payload)
        variable = sp.Symbol(var_name)
        expression = MathExpressionParser.parse_expr(payload.get("expression"), [var_name])
        func = sp.lambdify(variable, expression, modules=["numpy"])
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

        point = float(MathExpressionParser.parse_bound(payload.get("point"), "point", [var_name]))
        root = optimize.root(lambda values: [func(values[0])], [point])
        if not root.success:
            raise MathSolverError("numeric root search did not converge.")
        return float(root.x[0]), float(root.x[0])

    @staticmethod
    def _numeric_minimize(payload: dict[str, Any]) -> tuple[Any, Any]:
        var_name = MathPayloadReader.variable_name(payload)
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
        names = MathPayloadReader.variable_names(payload, default=("x", "y"))
        symbols = [sp.Symbol(name) for name in names]
        expression = MathExpressionParser.parse_expr(payload.get("expression"), names)
        func = sp.lambdify(symbols, expression, modules=["numpy"])
        initial = OptimizationPayloadAdapter.numeric_values(payload["initial_guess"], name="initial_guess")
        if initial.size != len(names):
            raise MathSolverError("initial_guess length must match variables.")

        bounds = OptimizationPayloadAdapter.bounds(payload, len(names))
        constraints = [
            {"type": "ineq", "fun": OptimizationPayloadAdapter.constraint_function(raw, names, symbols)}
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
        exact = {"x": np.asarray(result.x, dtype=float).tolist(), "fun": float(result.fun)}
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
        array = np.asarray(value, dtype=float)
        if array.ndim != 1 or array.size == 0:
            raise MathSolverError(f"{name} must be a non-empty 1D numeric array.")
        return array