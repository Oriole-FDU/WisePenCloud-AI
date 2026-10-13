"""图种子组件的路线、来源身份、并发与 Common Pipeline 接口。"""

import asyncio
import subprocess
import sys
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from common.utils.retrieval import Candidate, RetrievalPipeline, RrfFusion

from rag.core.schema.filters import MetadataFilter, MetadataFilters
from rag.retrievers import GraphMatch, GraphRetrievalMode, GraphRetriever
from rag.storage.graph import GraphVectorKind
from rag.storage.vector import VectorMatch


@pytest.fixture
def deps():
    return SimpleNamespace(
        entity_reader=SimpleNamespace(query=AsyncMock(return_value=[])),
        relationship_reader=SimpleNamespace(query=AsyncMock(return_value=[])),
        embedding_client=SimpleNamespace(
            dimensions=2, embed=AsyncMock(return_value=[[1.0, 0.0]])
        ),
    )


def build(deps, **options):
    return GraphRetriever.from_storage(**vars(deps), **options)


@pytest.mark.parametrize(
    "mode,kind",
    [
        (GraphRetrievalMode.LOCAL, GraphVectorKind.ENTITY),
        (GraphRetrievalMode.GLOBAL, GraphVectorKind.RELATIONSHIP),
    ],
)
async def test_single_route_uses_total_limit_and_preserves_provider_scores(
    deps, mode, kind
):
    reader = (
        deps.entity_reader
        if kind is GraphVectorKind.ENTITY
        else deps.relationship_reader
    )
    other = (
        deps.relationship_reader
        if kind is GraphVectorKind.ENTITY
        else deps.entity_reader
    )
    matches = [VectorMatch("first", 0.9), VectorMatch("second", 0.8)]
    reader.query.return_value = matches
    filters = MetadataFilters([MetadataFilter("ontology_name", "work")])
    result = await build(
        deps,
        mode=mode,
        candidate_limit=1,
        filters=filters,
        include_embedding=True,
        fusion=RrfFusion(),
    ).retrieve(" 查询 ")
    assert result == [Candidate(f"{kind}:first", GraphMatch(kind, matches[0]), 1, 0.9)]
    deps.embedding_client.embed.assert_awaited_once_with(texts=["查询"])
    request = reader.query.await_args.args[0]
    assert (
        request.top_k == 1 and request.filters is filters and request.include_embedding
    )
    assert request.embedding == [1.0, 0.0] and request.text is None
    other.query.assert_not_awaited()


@pytest.mark.parametrize("mode", ["local", "global"])
def test_unused_route_reader_is_not_required(deps, mode):
    arguments = (
        {"entity_reader": deps.entity_reader}
        if mode == "local"
        else {"relationship_reader": deps.relationship_reader}
    )
    GraphRetriever.from_storage(
        embedding_client=deps.embedding_client, mode=mode, **arguments
    )


async def test_hybrid_one_embedding_parallel_reads_and_round_robin(deps):
    started, completed = set(), asyncio.Event()

    async def query(kind, request):
        started.add(kind)
        if len(started) == 2:
            completed.set()
        await asyncio.wait_for(completed.wait(), 2)
        return [VectorMatch(f"{kind}-1", 0.9), VectorMatch(f"{kind}-2", 0.8)]

    async def entities(request):
        return await query("entity", request)

    async def relationships(request):
        return await query("relationship", request)

    deps.entity_reader.query.side_effect = entities
    deps.relationship_reader.query.side_effect = relationships
    result = await build(deps, candidate_limit=3).retrieve("查询")
    assert [candidate.payload.vector.record_id for candidate in result] == [
        "entity-1",
        "relationship-1",
        "entity-2",
    ]
    assert [candidate.rank for candidate in result] == [1, 2, 3]
    assert all(candidate.score == 0 for candidate in result)
    assert all(
        reader.query.await_args.args[0].top_k == 30
        for reader in (deps.entity_reader, deps.relationship_reader)
    )
    assert (
        deps.entity_reader.query.await_args.args[0].embedding
        is deps.relationship_reader.query.await_args.args[0].embedding
    )
    deps.embedding_client.embed.assert_awaited_once_with(texts=["查询"])


async def test_explicit_budgets_rrf_and_kind_namespace_preserve_sources(deps):
    # 相同原记录 ID 也不能把实体与关系当作同一候选；同逻辑实体不同来源保留。
    deps.entity_reader.query.return_value = [
        VectorMatch("same", 9.0, {"fact_id": "entity", "chunk_id": "a"}),
        VectorMatch("other-source", 8.0, {"fact_id": "entity", "chunk_id": "b"}),
    ]
    deps.relationship_reader.query.return_value = [VectorMatch("same", 7.0)]
    result = await build(
        deps,
        entity_top_k=2,
        relationship_top_k=4,
        candidate_limit=5,
        fusion=RrfFusion(),
    ).retrieve("查询")
    assert (
        len(result) == 3 and len({candidate.candidate_id for candidate in result}) == 3
    )
    assert all(candidate.score > 0 for candidate in result)
    assert deps.entity_reader.query.await_args.args[0].top_k == 2
    assert deps.relationship_reader.query.await_args.args[0].top_k == 4
    assert [
        item.payload.vector.metadata["chunk_id"]
        for item in result
        if item.payload.kind is GraphVectorKind.ENTITY
    ] == ["a", "b"]


@pytest.mark.parametrize(
    "options",
    [
        {"mode": "mix"},
        {"candidate_limit": 0},
        {"candidate_limit": True},
        {"entity_top_k": 0},
        {"relationship_top_k": True},
        {"mode": "local", "entity_top_k": 2},
        {"mode": "global", "relationship_top_k": 2},
    ],
)
def test_invalid_configuration_before_io(deps, options):
    with pytest.raises(ValueError):
        build(deps, **options)
    deps.embedding_client.embed.assert_not_called()


@pytest.mark.parametrize("mode", list(GraphRetrievalMode))
def test_selected_routes_and_embedding_required(deps, mode):
    with pytest.raises(ValueError):
        GraphRetriever.from_storage(mode=mode, embedding_client=deps.embedding_client)
    with pytest.raises(ValueError):
        GraphRetriever.from_storage(
            mode=mode,
            embedding_client=None,
            entity_reader=deps.entity_reader,
            relationship_reader=deps.relationship_reader,
        )


async def test_empty_query_before_io(deps):
    with pytest.raises(ValueError):
        await build(deps).retrieve("  ")
    deps.embedding_client.embed.assert_not_awaited()


@pytest.mark.parametrize(
    "response", [[], [[1.0]], [[float("nan"), 0.0]], [[True, 0.0]]]
)
async def test_embedding_boundary_errors_prevent_reader_io(deps, response):
    deps.embedding_client.embed.return_value = response
    with pytest.raises(RuntimeError, match="embedding"):
        await build(deps).retrieve("查询")
    deps.entity_reader.query.assert_not_awaited()
    deps.relationship_reader.query.assert_not_awaited()


@pytest.mark.parametrize("stage", ["embedding", "entity", "relationship", "cancel"])
async def test_backend_failure_and_cancellation_propagate(deps, stage):
    method = {
        "embedding": deps.embedding_client.embed,
        "entity": deps.entity_reader.query,
        "relationship": deps.relationship_reader.query,
        "cancel": deps.embedding_client.embed,
    }[stage]
    method.side_effect = (
        asyncio.CancelledError() if stage == "cancel" else OSError("backend failed")
    )
    with pytest.raises((OSError, asyncio.CancelledError)):
        await build(deps).retrieve("查询")


async def test_concurrent_queries_keep_vectors_request_local(deps):
    async def embed(*, texts):
        await asyncio.sleep(0)
        return [[1.0, 0.0] if texts == ["first"] else [0.0, 1.0]]

    deps.embedding_client.embed.side_effect = embed
    retriever = build(deps)
    await asyncio.gather(retriever.retrieve("first"), retriever.retrieve("second"))
    for reader in (deps.entity_reader, deps.relationship_reader):
        assert {
            tuple(call.args[0].embedding) for call in reader.query.await_args_list
        } == {(1.0, 0.0), (0.0, 1.0)}


async def test_common_pipeline_preparation_owns_authorization_before_reranking(deps):
    deps.entity_reader.query.return_value = [
        VectorMatch("hidden", 0.9),
        VectorMatch("visible", 0.8),
    ]
    prepared = []

    async def prepare(candidates):
        prepared.extend(candidates)
        return [
            replace(item, payload="已授权原文")
            for item in candidates
            if item.payload.vector.record_id == "visible"
        ]

    async def rerank(*, candidates, **kwargs):
        assert [candidate.payload for candidate in candidates] == ["已授权原文"]
        return candidates

    reranker = SimpleNamespace(rerank=AsyncMock(side_effect=rerank))
    pipeline = RetrievalPipeline(
        retriever=build(deps, mode="local"),
        prepare_candidates=prepare,
        reranker=reranker,
    )
    result = await pipeline.run(query="查询", top_k=5)
    assert len(prepared) == 2 and result == [
        Candidate("entity:visible", "已授权原文", 1, 0.8)
    ]


def test_retriever_dependency_boundary():
    subprocess.run(
        [
            sys.executable,
            "-c",
            """
import sys
from rag.retrievers.graph import GraphRetriever
assert not any(name.startswith(('rag.application', 'rag.ingestion', 'rag.backends', 'rag.core.config')) for name in sys.modules)
""",
        ],
        check=True,
    )
