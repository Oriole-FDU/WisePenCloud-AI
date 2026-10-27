"""论文渠道发现能力及其实际查询窗口。"""

from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import Protocol

from scholar.domain.models.paper import Paper


class DiscoveryPeriod(StrEnum):
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"


class DiscoverySort(StrEnum):
    RECENT = "recent"
    TRENDING = "trending"


@dataclass
class PaperDiscovery:
    papers: list[Paper]
    window_start: date | None
    window_end: date | None


class PaperDiscoverer(Protocol):
    async def discover(
        self,
        *,
        period: DiscoveryPeriod,
        sort: DiscoverySort,
        limit: int,
    ) -> PaperDiscovery: ...
