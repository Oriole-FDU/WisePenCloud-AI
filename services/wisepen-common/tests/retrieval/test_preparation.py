"""准备阶段是模型调用前的边界；业务 payload 只由调用方产生。"""

from dataclasses import dataclass
from unittest.mock import AsyncMock, Mock

import pytest
from common.utils.retrieval import (
    BM25Retriever,
    Candidate,
    Diversity,
    FusionRetriever,
    ModelReranker,
    RetrievalPipeline,
    Retriever,
    RoundRobinFusion,
    RrfFusion,
)


@dataclass(frozen=True)
class Payload:
    text: str


def stages(items):
    retriever = Retriever()
    retriever.retrieve = AsyncMock(return_value=items)
    reranker = Mock()
    reranker.rerank = AsyncMock(side_effect=lambda **kw: kw["candidates"])
    diversity = Diversity()
    diversity.diversify = Mock(wraps=diversity.diversify)
    return retriever, reranker, diversity


@pytest.mark.asyncio
async def test_preparation_changes_payload_before_model_and_preserves_scores():
    source = Candidate("a", {"text": "正文"}, rank=8, score=4.2)
    retriever, reranker, diversity = stages([source])
    payload = Payload("正文")

    async def prepare(items):
        assert items == [source]
        reranker.rerank.assert_not_awaited()
        return [Candidate("a", payload, rank=3, score=source.score)]

    result = await RetrievalPipeline(
        retriever=retriever,
        prepare_candidates=prepare,
        reranker=reranker,
        diversity=diversity,
    ).run(query=" query ", top_k=2)
    assert reranker.rerank.call_args.kwargs["candidates"][0].payload == payload
    assert result == [Candidate("a", payload, rank=1, score=4.2)]
    retriever.retrieve.assert_awaited_once_with(query="query")


@pytest.mark.asyncio
@pytest.mark.parametrize("empty_stage", ["recall", "prepare"])
async def test_empty_candidates_skip_later_stages(empty_stage):
    retriever, reranker, diversity = stages(
        [] if empty_stage == "recall" else [Candidate("a", "a", 1)]
    )
    prepare = AsyncMock(return_value=[])
    assert (
        await RetrievalPipeline(
            retriever=retriever,
            prepare_candidates=prepare,
            reranker=reranker,
            diversity=diversity,
        ).run(query="query", top_k=2)
        == []
    )
    assert prepare.await_count == int(empty_stage == "prepare")
    reranker.rerank.assert_not_awaited()
    diversity.diversify.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("failed_stage", ["recall", "prepare", "diversity"])
async def test_non_reranking_failure_propagates(failed_stage):
    retriever, reranker, diversity = stages([Candidate("a", "a", 1)])
    error = OSError(failed_stage)
    prepare = AsyncMock(side_effect=lambda items: items)
    if failed_stage == "recall":
        retriever.retrieve.side_effect = error
    elif failed_stage == "prepare":
        prepare.side_effect = error
    else:
        diversity.diversify.side_effect = error
    with pytest.raises(OSError) as caught:
        await RetrievalPipeline(
            retriever=retriever,
            prepare_candidates=prepare,
            reranker=reranker,
            diversity=diversity,
        ).run(query="query", top_k=2)
    assert caught.value is error
    if failed_stage != "diversity":
        reranker.rerank.assert_not_awaited()


@pytest.mark.asyncio
async def test_pipeline_reranking_failure_keeps_original_scores_and_payload():
    payload = Payload("正文")
    source = Candidate("a", payload, 9, -2.0)
    retriever, reranker, diversity = stages([source])
    reranker.rerank.side_effect = RuntimeError("failed")
    result = await RetrievalPipeline(
        retriever=retriever, reranker=reranker, diversity=diversity
    ).run(query="query", top_k=1)
    assert result == [Candidate("a", payload, 1, -2.0)]


@pytest.mark.asyncio
async def test_model_reranker_extracts_text_and_only_rewrites_ranking_fields():
    client = Mock(model="model")
    client.rerank = AsyncMock(return_value=[{"index": 1, "relevance_score": 0.9}])
    items = [Candidate("a", Payload("甲"), 1, 3), Candidate("b", Payload("乙"), 2, 2)]
    result = await ModelReranker(
        client=client, text_getter=lambda payload: payload.text, top_n=1
    ).rerank("focus", items)
    client.rerank.assert_awaited_once_with(
        query="focus", documents=["甲", "乙"], instruct=None, top_n=1
    )
    assert [item.candidate_id for item in result] == ["b", "a"]
    assert [item.rank for item in result] == [1, 2]
    assert result[0].payload == items[1].payload
    assert result[0].score == 0.9 and result[1].score == 3


@pytest.mark.asyncio
async def test_bm25_extracts_payload_text_without_replacing_payload():
    items = [Candidate("a", Payload("apple"), 1), Candidate("b", Payload("banana"), 2)]
    result = await BM25Retriever(
        candidates=items, text_getter=lambda payload: payload.text, top_k=1
    ).retrieve("banana")
    assert result[0].candidate_id == "b"
    assert result[0].payload == items[1].payload


def test_rrf_deduplicates_each_branch_and_keeps_first_payload_on_ties():
    a = Payload("first")
    result = RrfFusion().fuse(
        [
            [Candidate("a", a, 1), Candidate("a", Payload("duplicate"), 2)],
            [Candidate("b", Payload("second"), 1)],
        ]
    )
    assert [item.candidate_id for item in result] == ["a", "b"]
    assert result[0].payload == a
    assert result[0].score == result[1].score == pytest.approx(1 / 61)


@pytest.mark.asyncio
async def test_fusion_propagates_branch_failure():
    first, _, _ = stages([Candidate("a", Payload("a"), 1)])
    second, _, _ = stages([])
    error = OSError("sparse unavailable")
    second.retrieve.side_effect = error
    with pytest.raises(OSError) as caught:
        await FusionRetriever(
            retrievers=[first, second], fusion=RoundRobinFusion(), top_k=1
        ).retrieve("query")
    assert caught.value is error


@pytest.mark.parametrize("budget", [0, -1])
def test_fusion_rejects_invalid_constructor_budget(budget):
    with pytest.raises(ValueError):
        FusionRetriever(retrievers=[], top_k=budget)
