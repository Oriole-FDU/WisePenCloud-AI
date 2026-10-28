"""按规范身份批量读取完整论文的来源能力。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from scholar.domain.models.identifiers import PaperIdentifier
from scholar.domain.models.paper import Paper


@dataclass
class PaperLookupMatch:
    """与一个输入位置对应；零篇为未找到，多篇为歧义，错误不等于未找到。"""

    papers: list[Paper]
    error: str | None = None


class PaperLookupProvider(Protocol):
    async def lookup(
        self, ids: list[PaperIdentifier], *, api_key: str | None = None
    ) -> list[PaperLookupMatch]:
        """按输入顺序返回完整基础信息和来源 metadata，不做出口裁剪。"""
        ...
