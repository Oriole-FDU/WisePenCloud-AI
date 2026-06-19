from __future__ import annotations

from typing import Iterable

import sympy as sp
from sympy.core.relational import Relational
from sympy.parsing.sympy_parser import (
    convert_xor,
    implicit_multiplication_application,
    parse_expr,
    standard_transformations,
)

from chat.application.tools.math_tools.services.errors import MathSolverError
from chat.application.tools.tool_settings import tool_settings

MAX_EXPRESSION_CHARS = tool_settings.MATH_TOOL_MAX_EXPRESSION_CHARS

_TRANSFORMATIONS = standard_transformations + (
    implicit_multiplication_application,
    convert_xor,
)

_SAFE_GLOBALS = {
    "__builtins__": {},
    "Integer": sp.Integer,
    "Rational": sp.Rational,
    "Float": sp.Float,
    "Symbol": sp.Symbol,
    "Add": sp.Add,
    "Mul": sp.Mul,
    "Pow": sp.Pow,
}

_ALLOWED_NAMES = {
    "sin": sp.sin,
    "cos": sp.cos,
    "tan": sp.tan,
    "asin": sp.asin,
    "acos": sp.acos,
    "atan": sp.atan,
    "sinh": sp.sinh,
    "cosh": sp.cosh,
    "tanh": sp.tanh,
    "exp": sp.exp,
    "log": sp.log,
    "ln": sp.log,
    "sqrt": sp.sqrt,
    "abs": sp.Abs,
    "Abs": sp.Abs,
    "pi": sp.pi,
    "e": sp.E,
    "E": sp.E,
    "oo": sp.oo,
}

_ODE_NAMES = {
    "Derivative": sp.Derivative,
    "diff": sp.diff,
    "Eq": sp.Eq,
}


class MathExpressionParser:
    """数学表达式安全解析边界。

    该类集中管理 SymPy 解析白名单，避免各个 solver 自行开放名称或调用 `parse_expr`。
    """

    @staticmethod
    def parse_expr(
        expression: str | None,
        variables: Iterable[str] | None = None,
        *,
        extra_locals: dict[str, object] | None = None,
    ) -> sp.Expr:
        """安全解析数学表达式。

        Args:
            expression: 调用方传入的表达式字符串。
            variables: 显式允许的变量名。
            extra_locals: 特定 task 额外开放的 SymPy 名称。

        Returns:
            解析后的 SymPy 表达式。

        Raises:
            MathSolverError: 表达式为空、过长或包含不允许的语法。
        """
        if not isinstance(expression, str) or not expression.strip():
            raise MathSolverError("expression must be a non-empty string.")
        if len(expression) > MAX_EXPRESSION_CHARS:
            raise MathSolverError("expression is too long.")
        if "__" in expression or "import" in expression or "lambda" in expression:
            raise MathSolverError("unsafe expression.")
        if "'" in expression or '"' in expression:
            raise MathSolverError("string literals are not valid math expressions.")

        local_dict: dict[str, object] = dict(_ALLOWED_NAMES)
        for name in variables or ():
            if not name.isidentifier():
                raise MathSolverError(f"invalid variable name: {name}")
            local_dict[name] = sp.Symbol(name)
        if extra_locals:
            local_dict.update(extra_locals)

        try:
            return parse_expr(
                expression,
                local_dict=local_dict,
                global_dict=_SAFE_GLOBALS,
                transformations=_TRANSFORMATIONS,
                evaluate=True,
            )
        except Exception as e:
            raise MathSolverError(f"failed to parse expression: {e}") from e

    @staticmethod
    def parse_equation(
        equation: str | None,
        variables: Iterable[str] | None = None,
        *,
        extra_locals: dict[str, object] | None = None,
    ) -> sp.Equality:
        """解析 `left = right` 或 SymPy 关系表达式为 Eq。"""
        if not isinstance(equation, str) or not equation.strip():
            raise MathSolverError("equation must be a non-empty string.")
        if "=" in equation and "==" not in equation:
            left, right = equation.split("=", 1)
            return sp.Eq(
                MathExpressionParser.parse_expr(left, variables, extra_locals=extra_locals),
                MathExpressionParser.parse_expr(right, variables, extra_locals=extra_locals),
            )
        parsed = MathExpressionParser.parse_expr(equation, variables, extra_locals=extra_locals)
        if isinstance(parsed, sp.Equality):
            return parsed
        return sp.Eq(parsed, 0)

    @staticmethod
    def parse_inequality(expression: str | None, variable: str) -> Relational:
        """解析一元不等式表达式。"""
        parsed = MathExpressionParser.parse_expr(expression, [variable])
        if not isinstance(parsed, Relational):
            raise MathSolverError("solve_inequality requires a relational expression such as x^2 < 4.")
        return parsed

    @staticmethod
    def parse_ode_equation(
        equation: str | None,
        *,
        function_name: str,
        variable_name: str,
    ) -> tuple[sp.Equality, sp.Expr]:
        """解析常微分方程，并返回方程和待求函数。"""
        if not function_name.isidentifier():
            raise MathSolverError(f"invalid function name: {function_name}")
        if not variable_name.isidentifier():
            raise MathSolverError(f"invalid variable name: {variable_name}")
        variable = sp.Symbol(variable_name)
        function = sp.Function(function_name)
        func_expr = function(variable)
        extra_locals = {
            **_ODE_NAMES,
            variable_name: variable,
            function_name: function,
        }
        return MathExpressionParser.parse_equation(
            equation,
            [variable_name],
            extra_locals=extra_locals,
        ), func_expr

    @staticmethod
    def parse_bound(value: str | None, name: str, variables: Iterable[str] | None = None) -> sp.Expr:
        """解析积分、求和或数值区间边界。"""
        try:
            return MathExpressionParser.parse_expr(value, variables)
        except MathSolverError as e:
            raise MathSolverError(f"{name} must be a non-empty math expression string.") from e