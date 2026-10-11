from __future__ import annotations

from typing import TYPE_CHECKING, Generic, Literal, TypeVar

if TYPE_CHECKING:
    from ..retrievers.base import Candidate


PayloadT = TypeVar("PayloadT")


class Diversity(Generic[PayloadT]):
    """多样化基类；默认对所有模式原样传递候选。"""

    name = "base"

    def diversify(
        self,
        candidates: list[Candidate[PayloadT]],
        mode: Literal["off", "moderate", "high"],
    ) -> list[Candidate[PayloadT]]:
        del mode
        return candidates
