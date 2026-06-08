from typing import Any, Dict

import httpx

from chat.application.tools.math_solver.services.sage_runtime.schemas import (
    SageComputeResponse,
)
from common.logger import log_error


class SageRuntimeClient:
    def __init__(
        self,
        http_client: httpx.AsyncClient,
        *,
        base_url: str,
    ) -> None:
        self._client = http_client
        self._base_url = base_url

    async def compute_async(self, request: Dict[str, Any]) -> SageComputeResponse:
        try:
            response = await self._client.post(
                f"{self._base_url}/compute",
                json=request,
            )
            response.raise_for_status()
            result = SageComputeResponse.model_validate(response.json())
            return result
        except httpx.TimeoutException:
            log_error("sage_math_solver", "timeout", task=request.get("task"))
            return SageComputeResponse(
                status="error",
                task=str(request.get("task")),
                error="SageMath worker timed out.",
            )
        except Exception as e:
            log_error("sage_math_solver", e, task=request.get("task"))
            return SageComputeResponse(
                status="error",
                task=str(request.get("task")),
                error=f"SageMath worker request failed: {e}",
            )

    async def close(self) -> None:
        await self._client.aclose()
