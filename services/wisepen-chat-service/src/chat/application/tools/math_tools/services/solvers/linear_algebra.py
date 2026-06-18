from __future__ import annotations

import asyncio
from typing import Any

import numpy as np

from chat.application.tools.math_tools.services.errors import MathSolverError
from chat.application.tools.math_tools.services.solvers.utils.reader import (
    read_latex,
    read_matrix,
    read_numeric_matrix,
    read_vector,
)
from chat.application.tools.math_tools.services.tasks import LinearAlgebraTask


class LinearAlgebraSolver:
    """线性代数精确和数值计算。"""

    async def solve(self, task: str, payload: dict[str, Any]) -> Any:
        return await asyncio.to_thread(self._solve_sync, task, payload)

    def _solve_sync(self, task: str, payload: dict[str, Any]) -> Any:
        task_type = LinearAlgebraTask(task)
        numeric: Any = None

        # 1. 数值矩阵分解与计算路由 (不走 SymPy 符号解析)
        if task_type in {
            LinearAlgebraTask.SVD,
            LinearAlgebraTask.QR_DECOMPOSITION,
            LinearAlgebraTask.MATRIX_POWER,
        }:
            exact = self._solve_numeric(task_type, payload)
            return exact

        # 2. 符号矩阵计算路由
        matrix = read_matrix(payload)

        match task_type:
            case LinearAlgebraTask.DETERMINANT:
                exact = matrix.det()

            case LinearAlgebraTask.TRACE:
                exact = matrix.trace()

            case LinearAlgebraTask.RANK:
                exact = matrix.rank()

            case LinearAlgebraTask.INVERSE:
                exact = matrix.inv()

            case LinearAlgebraTask.RREF:
                reduced, pivots = matrix.rref()
                exact = {"matrix": reduced, "pivots": list(pivots)}

            case LinearAlgebraTask.EIGENVALUES:
                exact = matrix.eigenvals()

            case LinearAlgebraTask.LINEAR_SOLVE:
                rhs = (
                    read_vector(payload)
                    if payload.get("vector") is not None
                    else read_matrix(payload, "matrix_b")
                )
                exact = matrix.gauss_jordan_solve(rhs)[0]

            case LinearAlgebraTask.MATRIX_MULTIPLY:
                exact = matrix * read_matrix(payload, "matrix_b")

            case LinearAlgebraTask.NULL_SPACE:
                exact = matrix.nullspace()

            case _:
                raise MathSolverError(f"unsupported linear algebra task: {task_type.value}")

        return read_latex(exact)

    @staticmethod
    def _solve_numeric(task: LinearAlgebraTask, payload: dict[str, Any]) -> Any:
        matrix = read_numeric_matrix(payload)

        if task is LinearAlgebraTask.SVD:
            u, singular_values, vh = np.linalg.svd(matrix)
            return {
                "u": u.tolist(),
                "singular_values": singular_values.tolist(),
                "vh": vh.tolist(),
            }

        if task is LinearAlgebraTask.QR_DECOMPOSITION:
            q, r = np.linalg.qr(matrix)
            return {"q": q.tolist(), "r": r.tolist()}

        if task is LinearAlgebraTask.MATRIX_POWER:
            return np.linalg.matrix_power(matrix, payload["power"]).tolist()

        raise MathSolverError(f"unsupported numeric linear algebra task: {task}")