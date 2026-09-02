"""P2-C 图谱检索的召回分流、权限终检和局部精排测试。"""

import pytest
from common.utils.document import SourceSpan
from common.utils.ranking import RankDecision, RankedCandidate, RankResult
from pydantic import BaseModel

from rag.application.document.models import DocumentMetadata
from rag.application.graph.models import GraphNode, GraphPlugin, TextGraphEvidence
from rag.application.retrieval.graph_retriever import GraphRetriever
from rag.application.retrieval.models import (
    GraphSearchLevel,
    GraphSearchRequest,
)
from rag.domain.acl import PermissionScope, ResourceAcl
from rag.domain.repositories.graph_node_vectors import (
    GraphFilterCondition,
    GraphFilterOperator,
    GraphVectorCandidate,
)
from rag.domain.repositories.graph_topology import GraphSourceProjection

from .conftest import (
    MemoryAcls,
    MemoryDocChunks,
    MemoryDocuments,
    MemoryIndexStates,
    chunk_for_document,
    document,
)


class _Facts:
    def __init__(self, evidences=()) -> None:
        self.evidences = list(evidences)
        self.evidence_calls = []

    async def get_evidences(self, ids):
        self.evidence_calls.append(tuple(ids))
        return [item for item in self.evidences if item.evidence_id in ids]


class _Vectors:
    def __init__(self, candidates=()) -> None:
        self.candidates = list(candidates)
        self.dense_calls = []
        self.bm25_calls = []

    async def search_dense(self, **kwargs):
        self.dense_calls.append(kwargs)
        return self.candidates

    async def search_bm25(self, **kwargs):
        self.bm25_calls.append(kwargs)
        return self.candidates


class _Topology:
    def __init__(self, sources=()) -> None:
        self.sources = list(sources)
        self.calls = []

    async def traverse(self, **kwargs):
        self.calls.append(kwargs)
        return self.sources


class _Ranking:
    def __init__(self, decision=RankDecision.RELEVANT) -> None:
        self.decision = decision
        self.requests = []

    async def arank(self, request):
        self.requests.append(request)
        return RankResult(
            ranked=tuple(
                RankedCandidate(candidate=item, rank=index, score=1.0 / index)
                for index, item in enumerate(
                    request.candidates[: request.top_k], start=1
                )
            ),
            total_candidates=len(request.candidates),
            decision=self.decision,
        )


class _Embeddings:
    async def create(self, **kwargs):
        return type(
            "Response", (), {"data": [type("Item", (), {"embedding": [0.1, 0.2]})]}
        )()


class _OpenAI:
    embeddings = _Embeddings()


class _PaperFilter(BaseModel):
    year_from: int


class _FilterCompiler:
    filter_type = _PaperFilter

    def compile(self, value):
        return (
            GraphFilterCondition(
                field="reference_year",
                operator=GraphFilterOperator.GTE,
                value=value.year_from,
            ),
        )


class _PaperMetadata(DocumentMetadata):
    document_type: str = "paper"


def _plugin() -> GraphPlugin:
    from rag.application.graph.models import Ontology

    return GraphPlugin(
        plugin_id="paper",
        metadata_type=_PaperMetadata,
        ontology=Ontology(domain="paper"),
        filter_compiler=_FilterCompiler(),
    )


async def _active_source():
    item = document(
        resource_id="resource",
        version=1,
        section_id="section",
        raw_content="Alpha is proven.",
    )
    chunk = chunk_for_document(item)
    documents = MemoryDocuments()
    chunks = MemoryDocChunks()
    states = MemoryIndexStates()
    await documents.save_revision(item)
    await chunks.save_revision([chunk])
    await states.stage_revision(item.revision, expected_applied_content_revision=None)
    await states.apply_revision(item.revision)
    return (
        item,
        chunk,
        documents,
        chunks,
        states,
        MemoryAcls({"resource": ResourceAcl("resource", 1, "owner")}),
    )


def _retriever(
    *,
    topology,
    node_vectors,
    edge_vectors,
    facts,
    documents,
    chunks,
    states,
    acls,
    ranking,
    enabled=True,
):
    return GraphRetriever(
        enabled=enabled,
        topology=topology,
        node_vectors=node_vectors,
        edge_vectors=edge_vectors,
        graph_facts=facts,
        doc_chunks=chunks,
        documents=documents,
        index_states=states,
        resource_acls=acls,
        ranking_pipeline=ranking,
        plugins=(_plugin(),),
        openai_client=_OpenAI(),
        embedding_model="embedding",
        embedding_dimensions=2,
    )


@pytest.mark.asyncio
async def test_disabled_graph_search_touches_no_backend() -> None:
    topology = _Topology()
    vectors = _Vectors()
    ranking = _Ranking()
    retriever = _retriever(
        topology=topology,
        node_vectors=vectors,
        edge_vectors=vectors,
        facts=_Facts(),
        documents=MemoryDocuments(),
        chunks=MemoryDocChunks(),
        states=MemoryIndexStates(),
        acls=MemoryAcls({}),
        ranking=ranking,
        enabled=False,
    )

    result = await retriever.search(
        GraphSearchRequest(query="alpha"), PermissionScope("owner")
    )

    assert result.hits == []
    assert topology.calls == vectors.dense_calls == ranking.requests == []


@pytest.mark.asyncio
async def test_hybrid_retrieval_keeps_vector_branches_separate_and_returns_fact() -> (
    None
):
    item, _, documents, chunks, states, acls = await _active_source()
    node = GraphNode(node_id="node", name="Alpha", category="method")
    source = GraphSourceProjection(
        projection_id="source",
        target_type="node",
        target_id="node",
        resource_id=item.resource_id,
        content_revision=item.revision.content_revision,
        evidence_ids=(),
        producer_id="paper-v1",
        node=node,
        graph_rank=1,
    )
    node_vectors = _Vectors(
        [
            GraphVectorCandidate(
                "node-source",
                "node",
                "node",
                item.resource_id,
                item.revision.content_revision,
                1,
                "node_dense",
            )
        ]
    )
    edge_vectors = _Vectors(
        [
            GraphVectorCandidate(
                "edge-source",
                "edge",
                "edge",
                item.resource_id,
                item.revision.content_revision,
                1,
                "edge_dense",
            )
        ]
    )
    topology = _Topology([source])
    ranking = _Ranking()
    retriever = _retriever(
        topology=topology,
        node_vectors=node_vectors,
        edge_vectors=edge_vectors,
        facts=_Facts(),
        documents=documents,
        chunks=chunks,
        states=states,
        acls=acls,
        ranking=ranking,
    )

    result = await retriever.search(
        GraphSearchRequest(
            query="alpha method",
            level=GraphSearchLevel.HYBRID,
            plugin_id="paper",
            metadata_filter=_PaperFilter(year_from=2020),
        ),
        PermissionScope("owner"),
    )

    assert len(node_vectors.dense_calls) == 1
    assert len(edge_vectors.dense_calls) == len(edge_vectors.bm25_calls) == 1
    assert topology.calls[0]["metadata_filters"][0].field == "reference_year"
    assert result.hits[0].producer_id == "paper-v1"
    assert ranking.requests[0].candidates[0].text == "Alpha"
    # 图谱候选确定后只读取一次本地 active/ACL；异步 ACL 投影的传播延迟
    # 不能通过同一请求内的重复 Mongo 查询消除。
    assert states.get_states_calls == 1
    assert acls.get_acls_calls == 1


@pytest.mark.asyncio
async def test_seed_skips_vector_recall_and_llm_evidence_returns_chunk_only_after_quote_check() -> (
    None
):
    item, chunk, documents, chunks, states, acls = await _active_source()
    evidence = TextGraphEvidence(
        evidence_id="evidence",
        target_type="node",
        target_id="node",
        resource_id=item.resource_id,
        content_revision=item.revision.content_revision,
        chunk_id=chunk.chunk_id,
        source_spans=(SourceSpan(0, 5),),
        quote_text="Alpha",
    )
    source = GraphSourceProjection(
        projection_id="source",
        target_type="node",
        target_id="node",
        resource_id=item.resource_id,
        content_revision=item.revision.content_revision,
        evidence_ids=("evidence",),
        producer_id=None,
        graph_rank=1,
    )
    vectors = _Vectors()
    topology = _Topology([source])
    ranking = _Ranking()
    retriever = _retriever(
        topology=topology,
        node_vectors=vectors,
        edge_vectors=vectors,
        facts=_Facts([evidence]),
        documents=documents,
        chunks=chunks,
        states=states,
        acls=acls,
        ranking=ranking,
    )

    result = await retriever.search(
        GraphSearchRequest(query="Alpha", seed_node_ids=("node",), max_depth=0),
        PermissionScope("owner"),
    )

    assert vectors.dense_calls == vectors.bm25_calls == []
    assert result.hits[0].chunk_id == chunk.chunk_id
    assert result.hits[0].graph_ids == ["node"]
    assert "Alpha is proven." in ranking.requests[0].candidates[0].text


@pytest.mark.asyncio
async def test_llm_source_with_invalid_quote_is_not_sent_to_reranker() -> None:
    item, chunk, documents, chunks, states, acls = await _active_source()
    evidence = TextGraphEvidence(
        evidence_id="evidence",
        target_type="node",
        target_id="node",
        resource_id=item.resource_id,
        content_revision=item.revision.content_revision,
        chunk_id=chunk.chunk_id,
        source_spans=(SourceSpan(0, 5),),
        quote_text="wrong",
    )
    source = GraphSourceProjection(
        projection_id="source",
        target_type="node",
        target_id="node",
        resource_id=item.resource_id,
        content_revision=item.revision.content_revision,
        evidence_ids=("evidence",),
        producer_id=None,
    )
    ranking = _Ranking()
    retriever = _retriever(
        topology=_Topology([source]),
        node_vectors=_Vectors(),
        edge_vectors=_Vectors(),
        facts=_Facts([evidence]),
        documents=documents,
        chunks=chunks,
        states=states,
        acls=acls,
        ranking=ranking,
    )

    result = await retriever.search(
        GraphSearchRequest(query="Alpha", seed_node_ids=("node",)),
        PermissionScope("owner"),
    )

    assert result.hits == []
    assert ranking.requests == []


def test_plugin_filter_requires_registered_matching_type() -> None:
    request = GraphSearchRequest(
        query="x", plugin_id="paper", metadata_filter=_PaperFilter(year_from=2020)
    )
    assert (
        _plugin().compile_filter(request.metadata_filter)[0].operator
        is GraphFilterOperator.GTE
    )
    with pytest.raises(ValueError, match="does not match plugin"):
        _plugin().compile_filter({"year_from": 2020})
