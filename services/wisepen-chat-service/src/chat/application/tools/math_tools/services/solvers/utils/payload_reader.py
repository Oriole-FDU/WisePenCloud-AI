from __future__ import annotations

from typing import Any

import numpy as np
import sympy as sp

from chat.application.tools.math_tools.services.errors import MathSolverError


class MathPayloadReader:
    """数学工具入参读取和第三方库边界适配。"""

    @staticmethod
    def variable_name(payload: dict[str, Any], default: str = "x") -> str:
        """读取单变量名。"""
        name = str(payload.get("variable") or default)
        if not name.isidentifier():
            raise MathSolverError(f"invalid variable name: {name}")
        return name

    @staticmethod
    def variable_names(payload: dict[str, Any], default: tuple[str, ...] = ("x",)) -> list[str]:
        """读取多变量名。"""
        raw = payload.get("variables") or list(default)
        names = [str(item) for item in raw]
        for name in names:
            if not name.isidentifier():
                raise MathSolverError(f"invalid variable name: {name}")
        return names

    @staticmethod
    def matrix(payload: dict[str, Any], key: str = "matrix") -> sp.Matrix:
        """将二维数组解析为 SymPy Matrix。"""
        try:
            return sp.Matrix([[sp.sympify(item) for item in row] for row in payload.get(key)])
        except Exception as e:
            raise MathSolverError(f"{key} must be a valid matrix.") from e

    @staticmethod
    def vector(payload: dict[str, Any], key: str = "vector") -> sp.Matrix:
        """将一维数组解析为 SymPy 列向量。"""
        try:
            return sp.Matrix([sp.sympify(item) for item in payload.get(key)])
        except Exception as e:
            raise MathSolverError(f"{key} must be a valid vector.") from e

    @staticmethod
    def numeric_matrix(payload: dict[str, Any], key: str = "matrix") -> np.ndarray:
        """将二维数组解析为 NumPy float matrix。"""
        value = payload.get(key)
        try:
            matrix = np.asarray(value, dtype=float)
        except Exception as e:
            raise MathSolverError(f"{key} must be a numeric matrix.") from e
        if matrix.ndim != 2:
            raise MathSolverError(f"{key} must be a 2D matrix.")
        return matrix

    @staticmethod
    def numeric_values(value: object, *, name: str) -> np.ndarray:
        """将一维数值序列解析为 NumPy array。"""
        try:
            array = np.asarray(value, dtype=float)
        except Exception as e:
            raise MathSolverError(f"{name} must be a numeric array.") from e
        if array.ndim != 1 or array.size == 0:
            raise MathSolverError(f"{name} must be a non-empty 1D numeric array.")
        return array