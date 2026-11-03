"""在线搜索来源的统一应用端口及其可收敛失败语义。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Protocol

from scholar.domain.models.fields import AcademicField
from scholar.domain.models.paper import Paper


@dataclass
class PaperSearchBatch:
    """来源论文及召回降级标记；诊断异常由降级处记录，不进入结果缓存。"""

    papers: list[Paper]
    retrieval_degraded: bool = False


class PaperSearcher(Protocol):
    async def search(
        self,
        *,
        query: str,
        field: AcademicField,
        published_after: date | None,
        published_before: date | None,
        limit: int,
        api_key: str | None = None,
    ) -> PaperSearchBatch: ...
