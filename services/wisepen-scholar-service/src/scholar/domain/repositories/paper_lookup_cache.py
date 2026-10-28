"""按来源和论文身份共享完整查询结果，不区分用途或调用凭证。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from scholar.domain.models.identifiers import PaperIdentifier
from scholar.domain.models.paper import Paper


@dataclass(frozen=True)
class CachedPaperLookup:
    """paper 为 None 表示已确认未找到；缓存未命中由 get_many 的 None 表示。"""

    paper: Paper | None
    cache_aliases: tuple[PaperIdentifier, ...]


class PaperLookupCache(Protocol):
    async def get_many(
            self, ids: list[PaperIdentifier]
    ) -> list[CachedPaperLookup | None]: ...

    async def put_many(self, entries: list[tuple[CachedPaperLookup, int]]) -> None:
        """每项携带 TTL 秒数；同一记录的全部身份别名一起发布。"""
        ...

    async def invalidate(self, identifiers: list[PaperIdentifier]) -> None: ...
