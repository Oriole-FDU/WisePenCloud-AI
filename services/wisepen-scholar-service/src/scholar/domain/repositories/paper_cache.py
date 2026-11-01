"""按来源和论文身份共享完整查询结果，不区分用途或调用凭证。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from scholar.domain.models.identifiers import PaperIdentifier
from scholar.domain.models.paper import Paper
from scholar.domain.interfaces.paper_lookup import PaperLookupMatch


@dataclass(frozen=True)
class CachedPaperLookup:
    """paper 为 None 表示已确认未找到；缓存未命中由 get_many 的 None 表示。"""

    paper: Paper | None
    cache_aliases: tuple[PaperIdentifier, ...]


class PaperCache(Protocol):
    """统一论文事实、标题索引与 PDF 补充事实；来源负结果互不替代。"""

    async def get_many(
        self, source: str, ids: list[PaperIdentifier]
    ) -> list[CachedPaperLookup | None]: ...

    async def put_many(self, source: str, entries: list[tuple[CachedPaperLookup, int]]) -> None:
        """每项携带 TTL 秒数；同一记录的全部身份别名一起发布。"""
        ...

    async def invalidate(self, source: str, identifiers: list[PaperIdentifier]) -> None: ...

    async def match_titles(self, titles: list[str], sources: list[str]) -> list[PaperLookupMatch]: ...

    async def index_titles(self, entries: list[tuple[str, list[PaperIdentifier]]]) -> None: ...

    async def get_pdf_many(self, dois: list[str]) -> dict[str, str | None]: ...

    async def put_pdf_many(self, entries: list[tuple[str, str | None, int]]) -> None: ...
