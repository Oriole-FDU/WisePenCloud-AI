"""在线搜索来源的统一应用端口及其可收敛失败语义。"""

from datetime import date
from typing import Protocol

from scholar.domain.models.fields import AcademicField
from scholar.domain.models.paper import Paper


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
    ) -> list[Paper]: ...
