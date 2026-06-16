from __future__ import annotations

import asyncio
from typing import Any

import numpy as np

from chat.application.tools.math_tools.services.errors import MathSolverError
from chat.application.tools.math_tools.services.solvers.utils.latex import latex_or_none
from chat.application.tools.math_tools.services.solvers.utils.payload_reader import MathPayloadReader
from chat.application.tools.math_tools.services.tasks import LinearAlgebraTask


class LinearAlgebraSolver:
    """线性代数精确和数值计算。"""

    async def solve(self, task: str, payload: dict[str, Any]) -> Any:
        return await asyncio.to_thread(self._solve_sync, task, payload)

    def _solve_sync(self, task: str, payload: dict[str, Any]) -> Any:
        task_type = LinearAlgebraTask(task)

        numeric: Any = None
        if task_type in {
            LinearAlgebraTask.SVD,
            LinearAlgebraTask.QR_DECOMPOSITION,
            LinearAlgebraTask.MATRIX_POWER,
        }:
            exact = self._solve_numeric(task_type, payload)
            numeric = exact
            latex = None
        else:
            matrix = MathPayloadReader.matrix(payload)
            if task_type is LinearAlgebraTask.DETERMINANT:
                exact = matrix.det()
            elif task_type is LinearAlgebraTask.TRACE:
                exact = matrix.trace()
            elif task_type is LinearAlgebraTask.RANK:
                exact = matrix.rank()
            elif task_type is LinearAlgebraTask.INVERSE:
                exact = matrix.inv()
            elif task_type is LinearAlgebraTask.RREF:
                reduced, pivots = matrix.rref()
                exact = {"matrix": reduced, "pivots": list(pivots)}
            elif task_type is LinearAlgebraTask.EIGENVALUES:
                exact = matrix.eigenvals()
            elif task_type is LinearAlgebraTask.LINEAR_SOLVE:
                rhs = (
                    MathPayloadReader.vector(payload)
                    if payload.get("vector") is not None
                    else MathPayloadReader.matrix(payload, "matrix_b")
                )
                exact = matrix.gauss_jordan_solve(rhs)[0]
            elif task_type is LinearAlgebraTask.MATRIX_MULTIPLY:
                exact = matrix * MathPayloadReader.matrix(payload, "matrix_b")
            elif task_type is LinearAlgebraTask.NULL_SPACE:
                exact = matrix.nullspace()
            else:
                raise MathSolverError(f"unsupported linear algebra task: {task_type.value}")
            latex = latex_or_none(exact)

        return numeric if numeric is not None else latex

    @staticmethod
    def _solve_numeric(task: LinearAlgebraTask, payload: dict[str, Any]) -> Any:
        matrix = MathPayloadReader.numeric_matrix(payload)
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