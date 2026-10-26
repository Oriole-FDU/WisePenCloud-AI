"""在线论文读模型的批量端口；应用层不依赖 ES DSL。"""

from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import Protocol

from scholar.domain.models.identifiers import PaperIdentifier
from scholar.domain.models.paper import Paper


class LookupFailureReason(StrEnum):
    SOURCE_ERROR = "source_error"


@dataclass
class PaperLookupMatch:
    """每个精确标识的读取结果；来源失败不能等同于已确认未找到。"""

    papers: list[Paper]
    failure_reason: LookupFailureReason | None = None


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
    async def lookup(
        self, identifiers: list[PaperIdentifier]
    ) -> list[PaperLookupMatch]: ...
