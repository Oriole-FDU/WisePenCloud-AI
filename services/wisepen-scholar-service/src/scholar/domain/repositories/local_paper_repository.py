"""本地论文存储读模型端口；面向在线搜索与批量身份查询。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Protocol

from scholar.domain.interfaces.paper_lookup import PaperLookupMatch
from scholar.domain.models.identifiers import PaperIdentifier
from scholar.domain.models.paper import Paper


@dataclass
class PaperCandidate:
    """仓储返回的候选及原生融合分数，只用于应用层排序，不进入公开论文 DTO。"""
    paper: Paper
    score: float


class LocalPaperRepository(Protocol):
    async def search(
        self,
        *,
        query: str,
        categories: list[str],
        published_after: date | None,
        published_before: date | None,
        limit: int,
        vector: list[float] | None = None,
        num_candidates: int = 1000,
        rrf_k: int = 60,
    ) -> list[PaperCandidate]: ...

    async def lookup(
        self, identifiers: list[PaperIdentifier]
    ) -> list[PaperLookupMatch]: ...
