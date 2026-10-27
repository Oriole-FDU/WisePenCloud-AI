"""论文富化能力的通用应用端口。"""

from dataclasses import dataclass
from typing import Any, Protocol

from scholar.domain.models.identifiers import PaperIdentifier
from scholar.domain.models.paper import Paper


@dataclass
class PaperEnrichment:
    identifiers: list[PaperIdentifier]
    metadata: dict[str, Any]
    paper: Paper | None = None


class PaperEnricher(Protocol):
    async def enrich(
        self, identifiers: list[PaperIdentifier], *, include_details: bool = False
    ) -> list[PaperEnrichment | None]: ...
