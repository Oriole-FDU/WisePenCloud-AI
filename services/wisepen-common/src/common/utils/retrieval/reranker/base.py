from __future__ import annotations

from typing import TYPE_CHECKING, Generic, TypeVar

if TYPE_CHECKING:
    from ..retrievers.base import Candidate


PayloadT = TypeVar("PayloadT")


class Reranker(Generic[PayloadT]):
    """精排基类；默认保留候选顺序和分数。"""

    name = "base"

    async def rerank(
        self,
        focus: str,
        candidates: list[Candidate[PayloadT]],
    ) -> list[Candidate[PayloadT]]:
        del focus
        return candidates
