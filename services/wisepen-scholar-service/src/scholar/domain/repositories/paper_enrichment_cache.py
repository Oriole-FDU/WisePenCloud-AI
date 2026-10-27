"""可按规范论文身份读写并失效来源富化记录的共享缓存端口。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from scholar.domain.interfaces.paper_enricher import PaperEnrichment
from scholar.domain.models.identifiers import PaperIdentifier


@dataclass(frozen=True)
class CachedPaperEnrichment:
    enrichment: PaperEnrichment | None
    cache_aliases: tuple[PaperIdentifier, ...]
    record_found: bool
    details_confirmed: bool


class PaperEnrichmentCache(Protocol):
    async def get(
        self, identifiers: list[PaperIdentifier], *, scope: str | None = None
    ) -> CachedPaperEnrichment | None: ...

    async def put(
        self,
        identifiers: list[PaperIdentifier],
        value: CachedPaperEnrichment,
        *,
        ttl_seconds: int,
        scope: str | None = None,
    ) -> None: ...

    async def invalidate(self, identifiers: list[PaperIdentifier]) -> None: ...
