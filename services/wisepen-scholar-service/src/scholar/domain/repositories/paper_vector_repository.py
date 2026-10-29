"""稠密候选召回端口，按相关性顺序返回 arXiv 身份。"""

from __future__ import annotations

from datetime import date
from typing import Protocol


class PaperVectorRepository(Protocol):
    async def search(
        self,
        *,
        vector: list[float],
        categories: list[str],
        published_after: date | None,
        published_before: date | None,
        limit: int,
        num_candidates: int,
    ) -> list[str]: ...
