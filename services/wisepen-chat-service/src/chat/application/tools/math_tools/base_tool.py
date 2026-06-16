from __future__ import annotations

from typing import Any, Protocol

from chat.application.tools.core import (
    ToolDefinition,
    ToolExecutionError,
    ToolLLMSpec,
    ToolParametersSchema,
    ToolPolicy,
    ToolRiskLevel,
)
from chat.application.tools.math_tools.services.errors import MathSolverError


class MathSolver(Protocol):
    async def solve(self, task: str, payload: dict[str, Any]) -> Any:
        ...


class MathSolverTool:
    """结构化数学工具的通用外壳。

    该外壳只负责工具协议、错误包装和 service 调度；普通返回值由统一工具渲染器递归处理。
    """

    __slots__ = ("_definition", "_name", "_solver")

    def __init__(
        self,
        *,
        name: str,
        description: str,
        parameters_schema: dict[str, Any],
        solver: MathSolver
    ) -> None:
        self._name = name
        self._solver = solver
        self._definition = ToolDefinition(
            llm_spec=ToolLLMSpec(
                name=name,
                description=description,
                parameters_schema=ToolParametersSchema(parameters_schema),
            ),
            policy=ToolPolicy(
                expose_by_default=False,
                persist_output=True,
                risk_level=ToolRiskLevel.LOW,
                timeout_seconds=20.0,
            ),
        )

    @property
    def definition(self) -> ToolDefinition:
        """返回工具元定义。"""
        return self._definition

    async def execute(self, context: dict[str, Any], **kwargs: Any) -> Any:
        """执行结构化数学任务，返回值交给统一工具渲染器递归处理。"""
        task = str(kwargs["task"])
        try:
            # 不在工具层手动转 dict/XML；统一工具渲染器会递归处理 dataclass 返回值。
            return await self._solver.solve(task, dict(kwargs))
        except ToolExecutionError:
            raise
        except MathSolverError as e:
            raise ToolExecutionError(
                reason=f"{self._name}_failed",
                detail_reason=e.message,
                retryable=e.retryable,
                metadata={"task": task},
            ) from e
        except Exception as e:
            raise ToolExecutionError(
                reason=f"{self._name}_failed",
                detail_reason=str(e),
                retryable=False,
                metadata={"task": task},
            ) from e
