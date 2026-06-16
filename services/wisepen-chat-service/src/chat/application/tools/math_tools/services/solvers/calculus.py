from __future__ import annotations

import asyncio
from typing import Any

import sympy as sp

from chat.application.tools.math_tools.services.errors import MathSolverError
from chat.application.tools.math_tools.services.solvers.utils.expression_parser import MathExpressionParser
from chat.application.tools.math_tools.services.solvers.utils.latex import latex_or_none
from chat.application.tools.math_tools.services.solvers.utils.payload_reader import MathPayloadReader
from chat.application.tools.math_tools.services.tasks import CalculusTask


class CalculusSolver:
    """微积分与常微分方程计算。"""

    async def solve(self, task: str, payload: dict[str, Any]) -> Any:
        return await asyncio.to_thread(self._solve_sync, task, payload)

    def _solve_sync(self, task: str, payload: dict[str, Any]) -> Any:
        task_type = CalculusTask(task)

        if task_type is CalculusTask.SOLVE_ODE:
            exact = self._solve_ode(payload)
        elif task_type is CalculusTask.LAPLACE_TRANSFORM:
            exact = self._laplace_transform(payload)
        else:
            var_name = MathPayloadReader.variable_name(payload)
            variables = set(MathPayloadReader.variable_names(payload, default=(var_name,)))
            variables.add(var_name)
            if task_type is CalculusTask.DOUBLE_INTEGRAL:
                variables.add(str(payload.get("variable2") or "y"))
            expression = MathExpressionParser.parse_expr(payload.get("expression"), sorted(variables))
            variable = sp.Symbol(var_name)

            if task_type in {CalculusTask.DIFFERENTIATE, CalculusTask.PARTIAL_DIFFERENTIATE}:
                exact = sp.diff(expression, variable)
            elif task_type is CalculusTask.INTEGRATE:
                exact = sp.integrate(expression, variable)
            elif task_type is CalculusTask.DEFINITE_INTEGRAL:
                lower = MathExpressionParser.parse_bound(
                    payload.get("lower_bound") or payload.get("lower"),
                    "lower_bound",
                    [var_name],
                )
                upper = MathExpressionParser.parse_bound(
                    payload.get("upper_bound") or payload.get("upper"),
                    "upper_bound",
                    [var_name],
                )
                exact = sp.integrate(expression, (variable, lower, upper))
            elif task_type is CalculusTask.LIMIT:
                point = MathExpressionParser.parse_bound(payload.get("point"), "point", [var_name])
                exact = sp.limit(expression, variable, point)
            elif task_type is CalculusTask.TAYLOR_SERIES:
                point = MathExpressionParser.parse_bound(payload.get("point") or "0", "point", [var_name])
                order = int(payload.get("order") or 6)
                exact = sp.series(expression, variable, point, order)
            elif task_type is CalculusTask.SUMMATION:
                lower = MathExpressionParser.parse_bound(payload.get("lower"), "lower", [var_name])
                upper = MathExpressionParser.parse_bound(payload.get("upper"), "upper", [var_name])
                exact = sp.summation(expression, (variable, lower, upper))
            elif task_type is CalculusTask.DOUBLE_INTEGRAL:
                exact = self._double_integral(payload, expression, var_name)
            else:
                raise MathSolverError(f"unsupported calculus task: {task_type.value}")

        return latex_or_none(exact)

    def _double_integral(self, payload: dict[str, Any], expression: sp.Expr, first_var_name: str) -> Any:
        second_var_name = str(payload.get("variable2") or "y")
        if not second_var_name.isidentifier():
            raise MathSolverError(f"invalid variable name: {second_var_name}")
        first_var = sp.Symbol(first_var_name)
        second_var = sp.Symbol(second_var_name)
        variables = [first_var_name, second_var_name]
        lower1 = MathExpressionParser.parse_bound(
            payload.get("lower_bound") or payload.get("lower"),
            "lower_bound",
            variables,
        )
        upper1 = MathExpressionParser.parse_bound(
            payload.get("upper_bound") or payload.get("upper"),
            "upper_bound",
            variables,
        )
        lower2 = MathExpressionParser.parse_bound(payload.get("lower2"), "lower2", variables)
        upper2 = MathExpressionParser.parse_bound(payload.get("upper2"), "upper2", variables)
        return sp.integrate(expression, (first_var, lower1, upper1), (second_var, lower2, upper2))

    @staticmethod
    def _solve_ode(payload: dict[str, Any]) -> Any:
        variable = MathPayloadReader.variable_name(payload)
        function_name = str(payload.get("function") or "y")
        equation, func_expr = MathExpressionParser.parse_ode_equation(
            payload.get("equation") or payload.get("expression"),
            function_name=function_name,
            variable_name=variable,
        )
        return sp.dsolve(equation, func=func_expr)

    @staticmethod
    def _laplace_transform(payload: dict[str, Any]) -> Any:
        variable = MathPayloadReader.variable_name(payload, default="t")
        transform_variable = str(payload.get("transform_variable") or "s")
        if not transform_variable.isidentifier():
            raise MathSolverError(f"invalid transform variable name: {transform_variable}")
        expression = MathExpressionParser.parse_expr(payload.get("expression"), [variable, transform_variable])
        result, convergence_plane, condition = sp.laplace_transform(
            expression,
            sp.Symbol(variable),
            sp.Symbol(transform_variable),
        )
        return {
            "transform": result,
            "convergence_plane": convergence_plane,
            "condition": condition,
        }