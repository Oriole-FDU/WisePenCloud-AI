from typing import Optional

import sympy as sp
from sympy.parsing.sympy_parser import (
    convert_xor,
    implicit_multiplication_application,
    parse_expr,
    standard_transformations,
)

MAX_EXPRESSION_CHARS = 2000

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

_ALLOWED_FUNCTIONS = {
    "sin": sp.sin,
    "cos": sp.cos,
    "tan": sp.tan,
    "asin": sp.asin,
    "acos": sp.acos,
    "atan": sp.atan,
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


class MathParseError(ValueError):
    pass


def parse_math_expr(expression: str, variables: Optional[list[str]] = None) -> sp.Expr:
    if not isinstance(expression, str) or not expression.strip():
        raise MathParseError("expression must be a non-empty string.")
    if len(expression) > MAX_EXPRESSION_CHARS:
        raise MathParseError("expression is too long.")
    if "__" in expression or "import" in expression or "lambda" in expression:
        raise MathParseError("unsafe expression.")
    if "'" in expression or '"' in expression:
        raise MathParseError("string literals are not valid math expressions.")

    local_dict = dict(_ALLOWED_FUNCTIONS)
    for name in variables or []:
        if not name.isidentifier():
            raise MathParseError(f"invalid variable name: {name}")
        local_dict[name] = sp.Symbol(name)

    try:
        return parse_expr(
            expression,
            local_dict=local_dict,
            global_dict=_SAFE_GLOBALS,
            transformations=_TRANSFORMATIONS,
            evaluate=True,
        )
    except Exception as e:
        raise MathParseError(f"failed to parse expression: {e}") from e

