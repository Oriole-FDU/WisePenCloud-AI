"""配置在构造时确定，查询与 embedding 只保存在调用局部变量中。"""

import asyncio
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from common.utils.retrieval import RrfFusion

from rag.core.schema import VectorMatch
from rag.core.schema.filters import MetadataFilter, MetadataFilters
from rag.retrieval.vector import VectorRetrievalMode, VectorRetriever


@pytest.fixture
def deps():
    return (
        SimpleNamespace(query=AsyncMock(return_value=[])),
        SimpleNamespace(dimensions=2, embed=AsyncMock(return_value=[[1.0, 0.0]])),
    )


def build(deps, **options):
    reader, embedding = deps
    return VectorRetriever.from_storage(
        reader=reader,
        embedding_client=embedding,
        **options,
    )


@pytest.mark.parametrize(
    "mode", [VectorRetrievalMode.DENSE, VectorRetrievalMode.SPARSE]
)
async def test_single_mode_queries_only_selected_route_and_preserves_scores(deps, mode):
    reader, embedding = deps
    matches = [VectorMatch("a", 8.0), VectorMatch("b", 7.0)]
    reader.query.return_value = matches
    filters = MetadataFilters([MetadataFilter("category", "paper")])
    result = await build(
        deps,
        mode=mode,
        candidate_limit=1,
        filters=filters,
        include_embedding=True,
        fusion=RrfFusion(),  # 单路没有融合阶段，不使用此策略。
    ).retrieve(" query ")
    reader.query.assert_awaited_once()
    request = reader.query.call_args.args[0]
    assert request.filters is filters and request.include_embedding
    assert request.top_k == 1
    assert result[0].payload is matches[0]
    assert len(result) == 1 and result[0].score == 8.0 and result[0].rank == 1
    if mode is VectorRetrievalMode.DENSE:
        embedding.embed.assert_awaited_once_with(texts=["query"])
        assert request.embedding == [1.0, 0.0] and request.text is None
    else:
        embedding.embed.assert_not_awaited()
        assert request.embedding is None and request.text == "query"


async def test_sparse_requires_only_vector_reader_and_uses_candidate_budget(deps):
    reader, _ = deps
    retriever = VectorRetriever.from_storage(
        reader=reader, mode="sparse", candidate_limit=4
    )
    await retriever.retrieve("query")
    reader.query.assert_awaited_once()
    assert reader.query.call_args.args[0].text == "query"
    assert reader.query.call_args.args[0].top_k == 4


@pytest.mark.parametrize(
    "mode", [VectorRetrievalMode.DENSE, VectorRetrievalMode.SPARSE]
)
@pytest.mark.parametrize("budget", [{"dense_top_k": 4}, {"sparse_top_k": 5}])
def test_single_route_rejects_hybrid_budget_parameters_before_io(deps, mode, budget):
    with pytest.raises(ValueError, match="only supported in hybrid"):
        build(deps, mode=mode, **budget)
    deps[0].query.assert_not_called()
    deps[1].embed.assert_not_called()


@pytest.mark.parametrize(
    "mode", [VectorRetrievalMode.DENSE, VectorRetrievalMode.HYBRID]
)
def test_embedding_modes_require_client_at_initialization(deps, mode):
    with pytest.raises(ValueError, match="embedding client"):
        VectorRetriever.from_storage(reader=deps[0], mode=mode)
    deps[0].query.assert_not_called()


@pytest.mark.parametrize("mode", ["unknown", ""])
def test_unknown_mode_fails_before_io(deps, mode):
    with pytest.raises(ValueError):
        build(deps, mode=mode)
    deps[0].query.assert_not_called()
    deps[1].embed.assert_not_called()


async def test_constructor_configuration_is_applied_to_both_independent_queries(deps):
    reader, embedding = deps
    filters = MetadataFilters([MetadataFilter("category", "paper")])
    await build(
        deps,
        dense_top_k=7,
        sparse_top_k=9,
        candidate_limit=11,
        filters=filters,
        include_embedding=True,
    ).retrieve(" 中文查询 ")
    embedding.embed.assert_awaited_once_with(texts=["中文查询"])
    requests = [call.args[0] for call in reader.query.call_args_list]
    dense = next(request for request in requests if request.embedding is not None)
    sparse = next(request for request in requests if request.text is not None)
    assert dense is not sparse
    assert dense.embedding == [1.0, 0.0] and dense.text is None and dense.top_k == 7
    assert sparse.text == "中文查询" and sparse.embedding is None and sparse.top_k == 9
    assert all(
        request.filters is filters and request.include_embedding for request in requests
    )


async def test_defaults_and_round_robin_preserve_provider_payload_scores(deps):
    reader, _ = deps
    a, b, c = VectorMatch("a", 8.0), VectorMatch("b", 7.0), VectorMatch("c", 0.3)
    reader.query.side_effect = lambda request: [a, b] if request.embedding else [c, a]
    retriever = build(deps)
    result = await retriever.retrieve("query")
    assert retriever.top_k == 60
    assert [item.candidate_id for item in result] == ["a", "c", "b"]
    assert [item.rank for item in result] == [1, 2, 3]
    assert [item.score for item in result] == [0.0, 0.0, 0.0]
    assert result[0].payload is a and result[0].payload.score == 8.0
    assert all(call.args[0].top_k == 30 for call in reader.query.call_args_list)
    assert all(
        not call.args[0].include_embedding for call in reader.query.call_args_list
    )

    # 每路初始分数也是 provider score，只有融合才替换阶段分数。
    branch = await build(deps, mode=VectorRetrievalMode.DENSE).retrieve("query")
    assert [item.score for item in branch] == [8.0, 7.0]
    assert [item.rank for item in branch] == [1, 2]


async def test_custom_fusion_and_fixed_candidate_limit(deps):
    reader, _ = deps
    reader.query.side_effect = lambda request: (
        [VectorMatch("a", 10), VectorMatch("b", 9)]
        if request.embedding
        else [VectorMatch("b", 0.2), VectorMatch("c", 0.1)]
    )
    result = await build(deps, fusion=RrfFusion(), candidate_limit=1).retrieve("query")
    assert len(result) == 1 and result[0].candidate_id == "b"
    assert result[0].score == pytest.approx(1 / 61 + 1 / 62)
    assert result[0].payload.score == 9


@pytest.mark.parametrize(
    "option",
    [
        {"dense_top_k": 0},
        {"sparse_top_k": -1},
        {"candidate_limit": 0},
    ],
)
def test_invalid_configuration_fails_without_io(deps, option):
    with pytest.raises(ValueError):
        build(deps, **option)
    deps[0].query.assert_not_called()
    deps[1].embed.assert_not_called()


@pytest.mark.parametrize("query", ["", " \n\t "])
async def test_blank_query_fails_before_embedding_and_storage(deps, query):
    with pytest.raises(ValueError):
        await build(deps).retrieve(query)
    deps[0].query.assert_not_awaited()
    deps[1].embed.assert_not_awaited()


async def test_provider_default_dimensions_need_no_separate_retriever_configuration(
    deps,
):
    deps[1].dimensions = None
    deps[1].embed.return_value = [[1.0, 0.0, 0.5]]
    await build(deps).retrieve("query")
    dense = next(
        call.args[0]
        for call in deps[0].query.call_args_list
        if call.args[0].embedding is not None
    )
    assert dense.embedding == [1.0, 0.0, 0.5]


@pytest.mark.parametrize(
    "response",
    [
        [],
        [[1.0, 0.0], [0.0, 1.0]],
        [[1.0]],
        [[float("nan"), 0.0]],
        [[float("inf"), 0.0]],
        [[True, 0.0]],
        [["1", 0.0]],
    ],
)
async def test_invalid_embedding_response_is_a_backend_failure(deps, response):
    deps[1].embed.return_value = response
    with pytest.raises(RuntimeError, match="embedding response"):
        await build(deps).retrieve("query")
    assert not any(
        call.args[0].embedding is not None for call in deps[0].query.call_args_list
    )


async def test_embedding_client_value_error_uses_indexer_failure_contract(deps):
    error = ValueError("wrong response count")
    deps[1].embed.side_effect = error
    with pytest.raises(RuntimeError, match="embedding response") as caught:
        await build(deps).retrieve("query")
    assert caught.value.__cause__ is error


@pytest.mark.parametrize("route", ["embedding", "dense", "sparse"])
async def test_each_route_failure_propagates_without_single_route_fallback(deps, route):
    error = OSError(route)
    if route == "embedding":
        deps[1].embed.side_effect = error
    else:

        async def query(request):
            if (request.embedding is not None) == (route == "dense"):
                raise error
            return [VectorMatch("success", 1.0)]

        deps[0].query.side_effect = query
    with pytest.raises(OSError) as caught:
        await build(deps).retrieve("query")
    assert caught.value is error


async def test_dense_and_sparse_execute_concurrently(deps):
    entered = 0
    both_entered = asyncio.Event()

    async def query(request):
        nonlocal entered
        entered += 1
        if entered == 2:
            both_entered.set()
        await both_entered.wait()
        return []

    deps[0].query.side_effect = query
    assert await asyncio.wait_for(build(deps).retrieve("query"), timeout=2) == []


@pytest.mark.parametrize("mode", list(VectorRetrievalMode))
async def test_same_retriever_concurrent_queries_do_not_share_query_state(deps, mode):
    async def embed(*, texts, **kwargs):
        await asyncio.sleep(0)
        return [[float(texts[0]), 0.0]]

    async def query(request):
        await asyncio.sleep(0)
        label = str(int(request.embedding[0])) if request.embedding else request.text
        return [VectorMatch(label, 1.0)]

    deps[0].query.side_effect = query
    deps[1].embed.side_effect = embed
    retriever = build(deps, mode=mode)
    results = await asyncio.gather(retriever.retrieve("1"), retriever.retrieve("2"))
    assert [[item.candidate_id for item in result] for result in results] == [
        ["1"],
        ["2"],
    ]


def test_vector_retriever_import_does_not_load_business_or_runtime_modules():
    # storage 包现有导出会加载内容端口；它不表示 Retriever 依赖正文读取能力。
    subprocess.run(
        [
            sys.executable,
            "-c",
            """
import sys
from rag.retrieval.vector import VectorRetriever
assert not any(name.startswith(('rag.application', 'rag.acl',
    'rag.core.config', 'rag.backends')) for name in sys.modules)
""",
        ],
        check=True,
    )
