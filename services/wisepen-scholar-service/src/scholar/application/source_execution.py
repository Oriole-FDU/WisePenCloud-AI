"""本次用例的来源执行情况，与可复用的论文事实分开。"""

from dataclasses import dataclass
from enum import StrEnum

from scholar.domain.models.paper import PaperSource


class SourceExecutionStatus(StrEnum):
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"


@dataclass
class SourceExecution:
    source: PaperSource
    cache_hit: bool
    coverage: str
    status: SourceExecutionStatus = SourceExecutionStatus.COMPLETED
