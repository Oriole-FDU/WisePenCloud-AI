from __future__ import annotations

from dataclasses import asdict
from typing import Any

from chat.application.tools.math_solver.services.errors import MathSolverError
from chat.application.tools.math_solver.services.models import MathSolverResult
from chat.application.tools.math_solver.services.sage_runtime.client import SageRuntimeClient


class SageMathSolverService:
    def __init__(self, client: SageRuntimeClient) -> None:
        self._client = client

    async def solve(self, request: Any) -> MathSolverResult:
        payload = {
            key: value
            for key, value in asdict(request).items()
            if value is not None
        }
        response = await self._client.compute_async(payload)

        if response.status != "ok":
            raise MathSolverError(response.error or "SageMath worker error.")

        notes = list(response.warnings)
        if response.metadata:
            notes.append(f"metadata: {response.metadata}")

        return MathSolverResult(
            task=request.task,
            backend="sage",
            exact_result=response.exact_result,
            numeric_result=response.numeric_result,
            latex_result=response.latex_result,
            notes=notes,
        )
