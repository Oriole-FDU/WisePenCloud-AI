"""本次用例的来源执行情况，与可复用的论文事实分开。"""

from dataclasses import dataclass
from enum import StrEnum


class SourceExecutionStatus(StrEnum):
    NOT_EXECUTED = "not_executed"
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"


@dataclass
class SourceExecution:
    source: str
    cache_hit: bool
    coverage: str
    status: SourceExecutionStatus = SourceExecutionStatus.COMPLETED
    error: str | None = None
