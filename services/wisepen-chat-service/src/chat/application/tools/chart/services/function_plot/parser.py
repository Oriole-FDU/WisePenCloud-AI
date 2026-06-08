import re

import sympy as sp
from sympy.parsing.sympy_parser import (
    convert_xor,
    implicit_multiplication_application,
    parse_expr,
    standard_transformations,
)

from chat.application.tools.chart.services.function_plot.errors import FunctionPlotError
from chat.application.tools.chart.services.function_plot.models import ParsedExpression

MAX_EXPRESSION_CHARS = 200
ALLOWED_VARIABLES = {"x", "y", "t"}
ALLOWED_FUNCTIONS = {
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
}
ALLOWED_CONSTANTS = {"pi": sp.pi, "E": sp.E}

_BANNED_SUBSTRINGS = ("__", "import", "lambda", "open", "exec", "eval")
_BANNED_CHARS = set(".[]{}")
_IDENTIFIER_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
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


class FunctionExpressionParser:
    """安全数学表达式解析器。

    该解析器负责把用户表达式转换为 SymPy 表达式。这里保留安全校验，
    因为 JSON schema 无法表达函数白名单、属性访问禁用、自由变量约束等规则。
    """

    def parse(self, expression: str, variables: list[str]) -> ParsedExpression:
        """解析单个数学表达式。

        Args:
            expression: 用户输入的数学表达式。
            variables: tool 层声明允许出现的变量名。

        Returns:
            包含原文、规范化表达式、SymPy 表达式和 LaTeX 的解析结果。

        Raises:
            FunctionPlotError: 表达式为空、过长、包含危险语法或无法解析。
        """
        if not isinstance(expression, str) or not expression.strip():
            raise FunctionPlotError("expression must be a non-empty string.")
        raw = expression.strip()
        if len(raw) > MAX_EXPRESSION_CHARS:
            raise FunctionPlotError("expression length must be less than or equal to 200 characters.")
        lowered = raw.lower()
        if any(token in lowered for token in _BANNED_SUBSTRINGS):
            raise FunctionPlotError("expression contains unsafe Python-like syntax.")
        if any(char in raw for char in _BANNED_CHARS):
            raise FunctionPlotError("expression contains unsupported characters.")
        if "'" in raw or '"' in raw:
            raise FunctionPlotError("string literals are not valid math expressions.")

        variable_set = set(variables)
        if not variable_set or not variable_set.issubset(ALLOWED_VARIABLES):
            raise FunctionPlotError("variables must only contain x, y, or t.")

        allowed_names = variable_set | set(ALLOWED_FUNCTIONS) | set(ALLOWED_CONSTANTS)
        for name in _IDENTIFIER_RE.findall(raw):
            if name not in allowed_names:
                raise FunctionPlotError(f"unsupported identifier in expression: {name}")

        # parse_expr 支持隐式乘法和 ^ 幂运算，但只允许访问受控 local/global dict。
        normalized = raw.replace("^", "**")
        local_dict = dict(ALLOWED_FUNCTIONS)
        local_dict.update(ALLOWED_CONSTANTS)
        for name in variables:
            local_dict[name] = sp.Symbol(name)

        try:
            sympy_expr = parse_expr(
                normalized,
                local_dict=local_dict,
                global_dict=_SAFE_GLOBALS,
                transformations=_TRANSFORMATIONS,
                evaluate=True,
            )
        except Exception as exc:
            raise FunctionPlotError(f"failed to parse expression: {exc}") from exc

        free_symbols = {str(symbol) for symbol in sympy_expr.free_symbols}
        if not free_symbols.issubset(variable_set):
            extra = ", ".join(sorted(free_symbols - variable_set))
            raise FunctionPlotError(f"expression uses undeclared variable(s): {extra}")

        return ParsedExpression(
            raw=raw,
            normalized=normalized,
            sympy_expr=sympy_expr,
            latex=sp.latex(sympy_expr),
            variables=tuple(sorted(free_symbols)),
        )
