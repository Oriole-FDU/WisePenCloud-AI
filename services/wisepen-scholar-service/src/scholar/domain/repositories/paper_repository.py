"""在线论文读模型的批量端口；应用层不依赖 ES DSL。"""

from __future__ import annotations

from datetime import date
from typing import Protocol

from scholar.domain.interfaces.paper_lookup import PaperLookupMatch
from scholar.domain.models.identifiers import PaperIdentifier
from scholar.domain.models.paper import Paper


class PaperRepository(Protocol):
    async def search(
        self,
        *,
        query: str,
        categories: list[str],
        published_after: date | None,
        published_before: date | None,
        limit: int,
    ) -> list[Paper]: ...

    async def search_vector(
        self,
        *,
        vector: list[float],
        categories: list[str],
        published_after: date | None,
        published_before: date | None,
        limit: int,
        num_candidates: int = 1000,
    ) -> list[Paper]: ...

    async def lookup(
        self, identifiers: list[PaperIdentifier]
    ) -> list[PaperLookupMatch]: ...
