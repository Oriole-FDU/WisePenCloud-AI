"""在线搜索来源的统一应用端口及其可收敛失败语义。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Protocol

from scholar.domain.models.fields import AcademicField
from scholar.domain.models.paper import Paper


@dataclass
class PaperSearchBatch:
    """来源返回的论文及本次部分失败；执行状态不属于可缓存的论文事实。"""

    papers: list[Paper]
    partial_error: str | None = None


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
