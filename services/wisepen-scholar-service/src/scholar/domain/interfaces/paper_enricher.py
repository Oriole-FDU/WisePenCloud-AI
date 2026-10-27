"""论文富化能力的通用应用端口。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from scholar.domain.models.identifiers import PaperIdentifier
from scholar.domain.models.paper import Paper


@dataclass
class PaperEnrichment:
    identifiers: list[PaperIdentifier]
    # 来源命名空间内的增量事实；平台不融合同名字段或做语义去重，provider 负责键唯一。
    metadata: dict[str, Any]
    paper: Paper | None = None


class PaperEnricher(Protocol):
    async def enrich(
        self,
        identifiers: list[PaperIdentifier],
        *,
        include_details: bool = False,
        api_key: str | None = None,
    ) -> list[PaperEnrichment | None]: ...
