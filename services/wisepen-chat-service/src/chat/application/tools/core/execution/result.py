from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from chat.application.tools.core.llm.invocation import ToolInvocation
from chat.domain.entities import VisionImage


@dataclass
class ToolExecutionError(Exception):
    reason: str
    detail_reason: str | None = None
    retryable: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        super().__init__(self.detail_reason or self.reason)

@dataclass(frozen=True)
class CacheableText:
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ToolOutput:
    content: str
    images: list[VisionImage] = field(default_factory=list)
    cacheable_texts: tuple[CacheableText, ...] = ()


@dataclass(frozen=True)
class ToolExecutionResult:
    tool_invocation: ToolInvocation
    tool_output: ToolOutput | None
    started_at: datetime
    finished_at: datetime
    tool_execution_error: ToolExecutionError | None = None
