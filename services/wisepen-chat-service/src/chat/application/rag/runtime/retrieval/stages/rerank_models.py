from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RerankableDocument:
    """Document sent to reranker."""

    id: str
    text: str


@dataclass(frozen=True, slots=True)
class RerankedDocument:
    """Reranked document."""

    id: str
    score: float
    rank: int
