from __future__ import annotations

from typing import Any

import numpy as np
import sympy as sp

from chat.application.tools.math_tools.services.errors import MathSolverError


def read_variable_name(payload: dict[str, Any], default: str = "x") -> str:
    name = str(payload.get("variable") or default)
    if not name.isidentifier():
        raise MathSolverError(f"invalid variable name: {name}")
    return name


def read_variable_names(
    payload: dict[str, Any],
    default: tuple[str, ...] = ("x",),
) -> list[str]:
    raw = payload.get("variables") or list(default)
    names = [str(item) for item in raw]
    for name in names:
        if not name.isidentifier():
            raise MathSolverError(f"invalid variable name: {name}")
    return names


def read_matrix(payload: dict[str, Any], key: str = "matrix") -> sp.Matrix:
    try:
        return sp.Matrix([[sp.sympify(item) for item in row] for row in payload.get(key)])
    except Exception as exc:
        raise MathSolverError(f"{key} must be a valid matrix.") from exc


def read_vector(payload: dict[str, Any], key: str = "vector") -> sp.Matrix:
    try:
        return sp.Matrix([sp.sympify(item) for item in payload.get(key)])
    except Exception as exc:
        raise MathSolverError(f"{key} must be a valid vector.") from exc


def read_numeric_matrix(payload: dict[str, Any], key: str = "matrix") -> np.ndarray:
    try:
        matrix = np.asarray(payload.get(key), dtype=float)
    except Exception as exc:
        raise MathSolverError(f"{key} must be a numeric matrix.") from exc
    if matrix.ndim != 2:
        raise MathSolverError(f"{key} must be a 2D matrix.")
    return matrix


def read_numeric_values(value: object, *, name: str) -> np.ndarray:
    try:
        array = np.asarray(value, dtype=float)
    except Exception as exc:
        raise MathSolverError(f"{name} must be a numeric array.") from exc
    if array.ndim != 1 or array.size == 0:
        raise MathSolverError(f"{name} must be a non-empty 1D numeric array.")
    return array


def read_latex(value: Any) -> str | None:
    if value is None:
        return None
    try:
        return str(sp.latex(value))
    except Exception:
        return None
