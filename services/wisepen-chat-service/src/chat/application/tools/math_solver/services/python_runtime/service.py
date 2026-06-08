import asyncio
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from typing import Any, Optional

import sympy as sp

from chat.application.tools.math_solver.services.errors import MathSolverError
from chat.application.tools.math_solver.services.models import MathSolverResult
from chat.application.tools.math_solver.services.python_runtime.engine import (
    PythonMathEngine,
)
from chat.application.tools.math_solver.services.python_runtime.enums import PythonMathTask
from common.logger import log_error

_SYMBOLIC_TASKS = frozenset({
    PythonMathTask.SIMPLIFY,
    PythonMathTask.EXPAND,
    PythonMathTask.FACTOR,
    PythonMathTask.SOLVE_EQUATION,
    PythonMathTask.SOLVE_SYSTEM,
    PythonMathTask.DIFFERENTIATE,
    PythonMathTask.INTEGRATE,
    PythonMathTask.DEFINITE_INTEGRAL,
    PythonMathTask.LIMIT,
    PythonMathTask.TAYLOR_SERIES,
    PythonMathTask.NUMERIC,
    PythonMathTask.SUMMATION,
})

_MATRIX_TASKS = frozenset({
    PythonMathTask.MATRIX_DETERMINANT,
    PythonMathTask.MATRIX_TRACE,
    PythonMathTask.MATRIX_RANK,
    PythonMathTask.MATRIX_INVERSE,
    PythonMathTask.MATRIX_RREF,
    PythonMathTask.MATRIX_EIGENVALUES,
    PythonMathTask.MATRIX_SOLVE,
    PythonMathTask.MATRIX_MULTIPLY,
})

_COMBINATORICS_PROBABILITY_TASKS = frozenset({
    PythonMathTask.FACTORIAL,
    PythonMathTask.BINOMIAL,
    PythonMathTask.PERMUTATION,
    PythonMathTask.BINOMIAL_PROBABILITY,
    PythonMathTask.EXPECTATION,
    PythonMathTask.VARIANCE,
})

_NUMERIC_TASKS = frozenset({
    PythonMathTask.POISSON_PROBABILITY,
    PythonMathTask.NORMAL_CDF,
    PythonMathTask.NUMERIC_ROOT,
    PythonMathTask.NUMERIC_MINIMIZE,
})


class PythonMathSolverService:
    def __init__(
        self,
        *,
        engine: PythonMathEngine,
        max_workers: int = 2,
    ) -> None:
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="python-math-solver",
        )
        self._engine = engine

    async def solve(self, request: Any) -> MathSolverResult:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            self._executor,
            partial(self._solve_sync, request),
        )

    async def close(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)

    def _solve_sync(self, request: Any) -> MathSolverResult:
        try:
            exact: Any
            numeric_source: Optional[Any] = None

            if request.task in _SYMBOLIC_TASKS:
                exact = self._engine.compute_symbolic(request.task, request)
                numeric_source = exact
            elif request.task in _MATRIX_TASKS:
                exact = self._engine.compute_matrix(request.task, request)
                numeric_source = exact
            elif request.task in _COMBINATORICS_PROBABILITY_TASKS:
                exact = self._engine.compute_combinatorics_or_probability(request.task, request)
                numeric_source = exact
            elif request.task in _NUMERIC_TASKS:
                exact, numeric_source = self._engine.compute_numeric(request.task, request)
            else:
                raise ValueError(f"unsupported python math task: {request.task}")

            latex_result = None
            if exact is not None:
                try:
                    latex_result = sp.latex(exact)
                except Exception:
                    pass
            numeric_result = None
            if numeric_source is not None:
                try:
                    numeric_result = f"{float(numeric_source):.12g}"
                except (TypeError, ValueError):
                    pass
            return MathSolverResult(
                task=request.task,
                backend="python",
                exact_result=exact,
                numeric_result=numeric_result,
                latex_result=latex_result,
            )
        except MathSolverError:
            raise
        except Exception as e:
            log_error("python_math_solver", e, task=request.task)
            raise MathSolverError(f"Computation failed: {e}") from e
