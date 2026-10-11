from __future__ import annotations

import pytest

from common.utils.retrieval import (
    BM25Retriever,
    Candidate,
    Fusion,
    FusionRetriever,
    MmrDiversity,
    Reranker,
    RetrievalPipeline,
    Retriever,
    RoundRobinFusion,
    RrfFusion,
)
from common.utils.retrieval.reranker import ModelReranker
from common.utils.retrieval.retrievers import tokenize


class RecordingRetriever(Retriever):
    def __init__(self, items: list[Candidate], top_k: int = 30) -> None:
        super().__init__(top_k=top_k)
        self.items = items
        self.queries: list[str] = []

    async def retrieve(self, query: str) -> list[Candidate]:
        self.queries.append(query)
        return self.items[:self.top_k]


class RecordingReranker(Reranker):
    def __init__(self) -> None:
        self.focuses: list[str] = []
        self.received: list[Candidate] = []

    async def rerank(
        self,
        focus: str,
        candidates: list[Candidate],
    ) -> list[Candidate]:
        self.focuses.append(focus)
        self.received = candidates
        return self.received


@pytest.mark.asyncio
async def test_pipeline_passes_query_to_retriever_and_focus_to_reranker() -> None:
    candidate = Candidate("a", "alpha", rank=1, score=0.8)
    retriever = RecordingRetriever([candidate], top_k=2)
    reranker = RecordingReranker()

    result = await RetrievalPipeline(
        retriever=retriever,
        reranker=reranker,
    ).run(query="召回查询", focus="精排需求", top_k=5)

    assert retriever.queries == ["召回查询"]
    assert reranker.focuses == ["精排需求"]
    assert reranker.received == [candidate]
    assert result == [result[0]]
    assert (result[0].candidate_id, result[0].rank, result[0].score) == ("a", 1, 0.8)


@pytest.mark.asyncio
async def test_pipeline_falls_back_to_query_and_default_stages_are_identity() -> None:
    candidate = Candidate("a", "alpha", rank=3, score=0.8)
    retriever = RecordingRetriever([candidate])
    reranker = RecordingReranker()

    result = await RetrievalPipeline(
        retriever=retriever,
        reranker=reranker,
    ).run(query="same query", focus="   ", top_k=1, diversity="moderate")

    assert reranker.focuses == ["same query"]
    assert (result[0].candidate_id, result[0].rank, result[0].score) == ("a", 1, 0.8)


def test_fusion_base_flattens_and_deduplicates_in_input_order() -> None:
    left = [Candidate("a", "a", rank=1, score=10), Candidate("b", "b", rank=2)]
    right = [Candidate("a", "duplicate", rank=1), Candidate("c", "c", rank=2)]

    fused = Fusion().fuse([left, right])

    assert [candidate.candidate_id for candidate in fused] == ["a", "b", "c"]
    assert [candidate.rank for candidate in fused] == [1, 2, 3]
    assert [candidate.score for candidate in fused] == [0.0, 0.0, 0.0]


def test_round_robin_interleaves_by_rank_and_deduplicates_ids() -> None:
    left = [
        Candidate("a", "a", rank=1, score=10),
        Candidate("b", "b", rank=2, score=9),
    ]
    right = [
        Candidate("c", "c", rank=1, score=0.1),
        Candidate("a", "a duplicate", rank=2, score=0.05),
        Candidate("d", "d", rank=3, score=0.01),
    ]

    fused = RoundRobinFusion().fuse([left, right])

    assert [candidate.candidate_id for candidate in fused] == ["a", "c", "b", "d"]
    assert [candidate.rank for candidate in fused] == [1, 2, 3, 4]
    assert [candidate.score for candidate in fused] == [0.0, 0.0, 0.0, 0.0]
    assert RoundRobinFusion().fuse([]) == []


def test_rrf_uses_rank_and_writes_fusion_score_to_candidate() -> None:
    left = [Candidate("a", "a", rank=1), Candidate("b", "b", rank=2)]
    right = [Candidate("b", "b", rank=1), Candidate("c", "c", rank=2)]

    fused = RrfFusion().fuse([left, right])

    assert [candidate.candidate_id for candidate in fused] == ["b", "a", "c"]
    assert fused[0].rank == 1
    assert fused[0].score == pytest.approx(1 / 61 + 1 / 62)


@pytest.mark.asyncio
async def test_fusion_retriever_composes_retrievers_and_owns_budget() -> None:
    first = RecordingRetriever(
        [Candidate("a", "a", rank=1), Candidate("b", "b", rank=2)],
        top_k=2,
    )
    second = RecordingRetriever(
        [Candidate("b", "b", rank=1), Candidate("c", "c", rank=2)],
        top_k=2,
    )

    retriever = FusionRetriever(
        retrievers=[first, second],
        fusion=RoundRobinFusion(),
        top_k=3,
    )
    result = await RetrievalPipeline(retriever=retriever).run(
        query="query",
        top_k=2,
    )

    assert [item.candidate_id for item in result] == ["a", "b"]
    assert first.queries == ["query"]
    assert second.queries == ["query"]


@pytest.mark.asyncio
async def test_fusion_retriever_defaults_to_base_fusion() -> None:
    first = RecordingRetriever([Candidate("a", "a", rank=1)], top_k=1)
    second = RecordingRetriever(
        [
            Candidate("a", "duplicate", rank=1),
            Candidate("b", "b", rank=2),
        ],
        top_k=2,
    )

    result = await FusionRetriever(
        retrievers=[first, second],
        top_k=3,
    ).retrieve(query="query")

    assert [item.candidate_id for item in result] == ["a", "b"]


@pytest.mark.asyncio
async def test_fusion_retriever_can_be_nested() -> None:
    first = RecordingRetriever([Candidate("a", "alpha", rank=1)], top_k=1)
    inner = FusionRetriever(
        retrievers=[first],
        fusion=RoundRobinFusion(),
        top_k=1,
    )
    outer = FusionRetriever(
        retrievers=[inner],
        fusion=RrfFusion(),
        top_k=1,
    )

    result = await RetrievalPipeline(retriever=outer).run(
        query="query",
        top_k=1,
    )

    assert result[0].candidate_id == "a"
    assert first.queries == ["query"]


def test_bm25_default_tokenizer_normalizes_unicode() -> None:
    assert "abc" in tokenize("ＡＢＣ")
    assert tokenize("123") == ["123"]
    assert BM25Retriever(candidates=[Candidate("a", "abc", rank=0)]).top_k == 30


def test_mmr_uses_vectors_and_supports_modes() -> None:
    candidates = [
        Candidate("a", "", rank=1, score=1.0, vector=[1.0, 0.0]),
        Candidate("b", "", rank=2, score=0.95, vector=[1.0, 0.0]),
        Candidate("c", "", rank=3, score=0.7, vector=[0.0, 1.0]),
    ]
    diversity = MmrDiversity()

    assert [item.candidate_id for item in diversity.diversify(candidates, "off")] == [
        "a",
        "b",
        "c",
    ]
    high = diversity.diversify(candidates, "high")
    assert high[0].candidate_id == "a"
    assert high[1].candidate_id == "c"
    assert [item.rank for item in high] == [1, 2, 3]


@pytest.mark.asyncio
async def test_model_reranker_failure_preserves_candidate_order() -> None:
    class FailingClient:
        model = "qwen3-rerank"

        async def rerank(self, **_: object) -> list[dict[str, int | float]]:
            raise RuntimeError("service unavailable")

    candidates = [
        Candidate("a", "alpha", rank=1, score=0.4),
        Candidate("b", "beta", rank=2, score=0.3),
    ]
    reranker = ModelReranker(client=FailingClient())  # type: ignore[arg-type]

    assert await reranker.rerank("focus", candidates) == candidates
