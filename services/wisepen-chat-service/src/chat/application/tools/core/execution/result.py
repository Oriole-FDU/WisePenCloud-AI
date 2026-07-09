from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, TYPE_CHECKING

from chat.application.tools.core.llm.invocation import ToolInvocation
from chat.application.tools.core.llm.renderer import RenderToolResult

if TYPE_CHECKING:
    from chat.application.tools.core.definition import ToolOutput


@dataclass
class ToolExecutionError(Exception):
    reason: str
    detail_reason: str | None = None
    retryable: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        super().__init__(self.detail_reason or self.reason)


@dataclass(frozen=True)
class ToolExecutionResult:
    tool_invocation: ToolInvocation
    tool_output: "ToolOutput | None"
    started_at: datetime
    finished_at: datetime
    tool_execution_error: ToolExecutionError | None = None


@dataclass(frozen=True)
class ToolBatchResult:
    results: list[RenderToolResult]
