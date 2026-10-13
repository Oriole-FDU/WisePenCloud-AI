"""图召回必须先授权，再组装分种子的完整来源材料。"""

import asyncio
from dataclasses import asdict, replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from common.utils.retrieval import ModelReranker

from rag.application.acl.models import ResourceAcl
from rag.application.graph import GraphRetrieval
from rag.application.retrieval import VectorRetrieval
from rag.core.schema import EntitySpec, Ontology, RelationSpec
from rag.core.schema.filters import (
    FilterCondition,
    FilterOperator,
    MetadataFilter,
    MetadataFilters,
)
from rag.ingestion.indexers import GraphIndexer
from rag.retrievers.graph import GraphRetrievalMode
from rag.storage.graph import GraphDirection, GraphVectorKind
from rag.storage.vector import VectorMatch


@pytest.mark.parametrize("mode", list(GraphRetrievalMode))
async def test_modes_group_sources_once_and_keep_full_material(graph_env, mode):
    env = graph_env
    env.entities[:] = [env.match(0, score=0.9), env.match(2, score=0.8)]
    env.relations[:] = [env.match(0, kind=GraphVectorKind.RELATIONSHIP)]
    hits = await env.retrieval.retrieve(" query ", snapshot=env.snapshot(), mode=mode)
    assert len(hits) == (2 if mode is GraphRetrievalMode.HYBRID else 1)
    assert [hit.rank for hit in hits] == list(range(1, len(hits) + 1))
    for hit in hits:
        evidence = hit.evidence
        assert {source.resource_id for source in evidence.sources} == {"a", "c"}
        assert {edge.description for edge in evidence.relationships} == {
            "a-任职",
            "c-任职",
        }
        assert {node.description for node in evidence.entities} == {
            "a-张三",
            "a-甲公司",
            "c-张三",
            "c-甲公司",
        }
        assert len(evidence.entities) == 4 and len(evidence.relationships) == 2
        assert all(
            source.raw_text
            == env.chunks[0 if source.resource_id == "a" else 2].raw_text
            for source in evidence.sources
        )
        assert set(asdict(evidence.sources[0])) == {
            "chunk_id",
            "resource_id",
            "content_revision",
            "section_id",
            "section_path",
            "raw_text",
        }
        assert "embedding" not in asdict(hit)
    env.embedding.embed.assert_awaited_once_with(texts=["query"])
    assert env.entity_reader.query.await_count == (
        mode is not GraphRetrievalMode.GLOBAL
    )
    assert env.relation_reader.query.await_count == (
        mode is not GraphRetrievalMode.LOCAL
    )
    assert env.graph.get_neighbors.await_count == (
        mode is not GraphRetrievalMode.GLOBAL
    )
    if mode is GraphRetrievalMode.LOCAL:
        assert hits[0].score == 0.9


async def test_shared_reads_do_not_broaden_global_endpoint_sources(graph_env, storage):
    env = graph_env
    storage.get_chunks = AsyncMock(wraps=storage.get_chunks)
    env.entities[:] = [env.match(3)]  # 公司实体组可以消费 b/d 的来源。
    env.relations[:] = [env.match(kind=GraphVectorKind.RELATIONSHIP)]
    hits = await env.retrieval.retrieve("query", snapshot=env.snapshot())
    entity, relation = [hit.evidence for hit in hits]
    assert {source.resource_id for source in entity.sources} == {"a", "b", "c", "d"}
    assert {source.resource_id for source in relation.sources} == {"a", "c"}
    assert all(node.description != "d-无关公司描述" for node in relation.entities)
    assert all(edge.relation_type == "works_at" for edge in relation.relationships)
    fetched = [
        key for call in storage.get_chunks.call_args_list for key in call.args[0]
    ]
    assert len(fetched) == len(set(fetched))  # 调用内一次读取与解析，不改变各组范围。


@pytest.mark.parametrize("hidden", ["denied", "destroyed", "missing-acl"])
async def test_hidden_sources_cannot_supply_candidates_or_neighborhood(
    graph_env, read_acls, publication_store, hidden
):
    env = graph_env
    if hidden == "denied":
        read_acls["a"] = ResourceAcl("a", 1, "other")
    elif hidden == "destroyed":
        await publication_store.destroy_resources(["a"])
    else:
        del read_acls["a"]
    env.entities[:] = [env.match(0), env.match(2)]
    hits = await env.retrieval.retrieve("query", snapshot=env.snapshot(), mode="local")
    assert {source.resource_id for source in hits[0].evidence.sources} == {"c"}
    assert all(
        node.source_id == env.chunks[2].chunk_id for node in hits[0].evidence.entities
    )
    # 只有不可见候选时，即使同逻辑 ID 在其他来源可见，也不能反向扩成种子。
    env.entities[:] = [env.match(0)]
    env.graph.get_neighbors.reset_mock()
    assert (
        await env.retrieval.retrieve("query", snapshot=env.snapshot(), mode="local")
        == []
    )
    env.graph.get_neighbors.assert_not_awaited()


@pytest.mark.parametrize(
    "field,value",
    [
        ("resource_id", "wrong"),
        ("content_revision", "wrong"),
        ("fact_id", "wrong"),
        ("fact_sha256", "old"),
        ("chunk_id", "missing"),
    ],
)
async def test_bad_vector_identity_is_not_repaired_from_mongo(graph_env, field, value):
    env = graph_env
    match = env.match()
    env.entities[:] = [replace(match, metadata={**match.metadata, field: value})]
    assert (
        await env.retrieval.retrieve("query", snapshot=env.snapshot(), mode="local")
        == []
    )
    env.graph.get_nodes.assert_not_awaited()
    env.graph.get_neighbors.assert_not_awaited()


async def test_wrong_record_id_and_missing_metadata_skip_content_io(graph_env, storage):
    env = graph_env
    storage.get_chunks = AsyncMock(wraps=storage.get_chunks)
    env.entities[:] = [
        replace(env.match(), record_id="wrong"),
        replace(env.match(), metadata={}),
    ]
    assert (
        await env.retrieval.retrieve("query", snapshot=env.snapshot(), mode="local")
        == []
    )
    storage.get_chunks.assert_not_awaited()


async def test_changed_description_invalidates_vector_but_projection_uses_current_mongo(
    graph_env, storage
):
    env = graph_env
    env.entities[:] = [env.match()]
    env.chunks[0].metadata["entities"][0]["description"] = "更新后的人员描述"
    await storage.upsert_chunks([env.chunks[0]])
    assert (
        await env.retrieval.retrieve("query", snapshot=env.snapshot(), mode="local")
        == []
    )
    # 通过未变化的公司种子验证拓扑材料始终采用当前来源事实。
    env.entities[:] = [env.match(0, position=1)]
    hits = await env.retrieval.retrieve("query", snapshot=env.snapshot(), mode="local")
    assert "更新后的人员描述" in {
        node.description for node in hits[0].evidence.entities
    }
    assert "a-张三" not in {node.description for node in hits[0].evidence.entities}


@pytest.mark.parametrize("state", ["absent", "empty", "partial", "structure", "source"])
async def test_missing_facts_skip_but_damaged_current_facts_raise(
    graph_env, storage, state
):
    env = graph_env
    env.entities[:] = [env.match()]
    chunk = env.chunks[0]
    if state == "absent":
        del chunk.metadata["entities"], chunk.metadata["relationships"]
    elif state == "empty":
        chunk.metadata.update(entities=[], relationships=[])
    elif state == "partial":
        del chunk.metadata["relationships"]
    elif state == "structure":
        chunk.metadata["entities"] = ["broken"]
    else:
        chunk.metadata["entities"][0]["source_id"] = "wrong"
    await storage.upsert_chunks([chunk])
    if state in ("absent", "empty"):
        assert (
            await env.retrieval.retrieve("query", snapshot=env.snapshot(), mode="local")
            == []
        )
    else:
        with pytest.raises((TypeError, ValueError)):
            await env.retrieval.retrieve("query", snapshot=env.snapshot(), mode="local")
    env.graph.get_neighbors.assert_not_awaited()


async def test_illegal_facts_and_missing_relation_are_dropped_individually(
    graph_env, storage
):
    env = graph_env
    env.entities[:] = [env.match()]
    env.chunks[0].metadata["entities"].append(
        {**env.chunks[0].metadata["entities"][0], "category": "Unknown"}
    )
    env.chunks[0].metadata["relationships"] = []
    await storage.upsert_chunks([env.chunks[0]])
    hits = await env.retrieval.retrieve("query", snapshot=env.snapshot(), mode="local")
    assert {edge.source_ids[0] for edge in hits[0].evidence.relationships} == {
        env.chunks[2].chunk_id
    }
    assert env.chunks[0].chunk_id in {
        node.source_id for node in hits[0].evidence.entities
    }


async def test_missing_topology_preserves_authorized_vector_seed(graph_env):
    env = graph_env
    env.entities[:] = [env.match()]
    env.graph.nodes = []
    env.graph.edges = []
    hits = await env.retrieval.retrieve("query", snapshot=env.snapshot(), mode="local")
    assert [node.id for node in hits[0].evidence.entities] == [env.facts[0][0][0].id]
    assert hits[0].evidence.relationships == []


@pytest.mark.parametrize("diversity", ["off", "moderate", "high"])
async def test_reranking_focus_mmr_and_first_source_embedding(graph_env, diversity):
    env = graph_env
    env.entities[:] = [
        env.match(0, score=1, embedding=[1.0, 0.0]),
        env.match(2, score=0.99, embedding=[0.0, 1.0]),  # 同 ID 不替换首个向量。
        env.match(0, position=1, score=0.9, embedding=[1.0, 0.0]),
        env.match(1, position=1, score=0.8, embedding=[0.0, 1.0]),
    ]
    client = SimpleNamespace(
        model="rerank", rerank=AsyncMock(return_value=[1, 0.9, 0.8])
    )
    reranker = ModelReranker(
        client=client, text_getter=lambda payload: payload.evidence.fact_id
    )
    usecase = GraphRetrieval(**env.dependencies, reranker=reranker)
    hits = await usecase.retrieve(
        "query",
        snapshot=env.snapshot(),
        mode="local",
        top_k=2,
        focus=" focus ",
        diversity=diversity,
    )
    assert [hit.evidence.fact_id for hit in hits] == [
        env.facts[0][0][0].id,
        env.facts[1 if diversity == "high" else 0][0][1].id,
    ]
    assert client.rerank.call_args.kwargs["query"] == "focus"
    assert len(client.rerank.call_args.kwargs["documents"]) == 3
    assert env.entity_reader.query.call_args.args[0].include_embedding == (
        diversity != "off"
    )


@pytest.mark.parametrize(
    "failure", ["embedding", "vector", "graph", "content", "cancel", "rerank"]
)
async def test_failures_and_cancel_propagate_except_common_reranker_fallback(
    graph_env, storage, failure
):
    env = graph_env
    env.entities[:] = [env.match()]
    error = asyncio.CancelledError() if failure == "cancel" else OSError("failed")
    if failure == "embedding":
        env.embedding.embed.side_effect = error
    elif failure == "vector":
        env.entity_reader.query.side_effect = error
    elif failure in ("graph", "cancel"):
        env.graph.get_neighbors.side_effect = error
    elif failure == "content":
        storage.get_chunks = AsyncMock(side_effect=error)
    reranker = (
        SimpleNamespace(rerank=AsyncMock(side_effect=error))
        if failure == "rerank"
        else None
    )
    usecase = GraphRetrieval(**env.dependencies, reranker=reranker)
    if failure == "rerank":
        assert (
            len(await usecase.retrieve("query", snapshot=env.snapshot(), mode="local"))
            == 1
        )
    else:
        with pytest.raises(type(error)) as caught:
            await usecase.retrieve("query", snapshot=env.snapshot(), mode="local")
        assert caught.value is error


async def test_same_snapshot_keeps_first_revision_and_visibility(
    graph_env, read_acls, publication_store, publish_read_document, make_read_document
):
    env = graph_env
    env.entities[:] = [env.match()]
    snapshot = env.snapshot()
    first = await env.retrieval.retrieve("query", snapshot=snapshot, mode="local")
    await publish_read_document(make_read_document("a", version=2))
    read_acls["c"] = ResourceAcl("c", 2, "other")
    await publication_store.destroy_resources(["a"])
    assert (
        await env.retrieval.retrieve("query", snapshot=snapshot, mode="local") == first
    )
    assert (
        await env.retrieval.retrieve("query", snapshot=env.snapshot(), mode="local")
        == []
    )


async def test_concurrent_calls_keep_request_state_separate(graph_env):
    env = graph_env
    env.entities[:] = [env.match()]
    allowed, denied = await asyncio.gather(
        env.retrieval.retrieve("allow", snapshot=env.snapshot(), mode="local"),
        env.retrieval.retrieve("deny", snapshot=env.snapshot("other"), mode="local"),
    )
    assert len(allowed) == 1 and denied == []
    assert env.graph.get_neighbors.await_count == 1


async def test_source_filters_match_vector_graph_and_authoritative_chunk(graph_env):
    env = graph_env
    env.entities[:] = [env.match(), env.match(2)]
    filters = MetadataFilters(
        [
            MetadataFilters(
                [MetadataFilter("resource_id", ["a", "c"], FilterOperator.IN)],
                FilterCondition.OR,
            ),
            MetadataFilters([MetadataFilter("resource_id", "a")], FilterCondition.NOT),
            MetadataFilter("content_revision", env.chunks[2].content_revision),
        ]
    )
    hits = await env.retrieval.retrieve(
        "query", snapshot=env.snapshot(), mode="local", filters=filters
    )
    assert {source.resource_id for source in hits[0].evidence.sources} == {"c"}
    assert env.entity_reader.query.call_args.args[0].filters is filters
    assert env.graph.get_nodes.call_args.kwargs["filters"] is filters
    assert env.graph.get_neighbors.call_args.kwargs["filters"] is filters


@pytest.mark.parametrize(
    "kwargs",
    [
        {"query": " "},
        {"top_k": 0},
        {"candidate_limit": 1},
        {"mode": "mix"},
        {"entity_top_k": 0},
        {"relationship_top_k": 0},
        {"mode": "local", "entity_top_k": 3},
        {"diversity": "bad"},
        {"neighbor_limit": 0},
        {"neighbor_direction": "bad"},
    ],
)
async def test_invalid_input_fails_before_io(graph_env, kwargs):
    env = graph_env
    with pytest.raises(ValueError):
        await env.retrieval.retrieve(
            snapshot=env.snapshot(), **{"query": "query", **kwargs}
        )
    env.embedding.embed.assert_not_awaited()
    env.entity_reader.query.assert_not_awaited()
    env.graph.get_nodes.assert_not_awaited()


async def test_single_route_only_requires_selected_reader_and_empty_recall_skips_graph(
    graph_env, storage
):
    env = graph_env
    dependencies = {**env.dependencies, "relationship_reader": None}
    usecase = GraphRetrieval(**dependencies)
    storage.get_chunks = AsyncMock(wraps=storage.get_chunks)
    assert await usecase.retrieve("query", snapshot=env.snapshot(), mode="local") == []
    storage.get_chunks.assert_not_awaited()
    env.graph.get_nodes.assert_not_awaited()
    with pytest.raises(ValueError):
        await usecase.retrieve("query", snapshot=env.snapshot(), mode="global")


async def test_indexed_source_vectors_reach_authorized_evidence_without_mutating_chunks(
    graph_env, graph_vector_writers
):
    env = graph_env
    before = [asdict(chunk) for chunk in env.chunks]
    entities, relations = graph_vector_writers
    env.embedding.embed.side_effect = lambda *, texts: [[1.0, 0.0] for _ in texts]
    await GraphIndexer(
        ontology=env.ontology,
        embedding_client=env.embedding,
        entity_writer=entities,
        relationship_writer=relations,
    ).index(env.chunks)
    record = next(iter(entities.records.values()))
    env.entities[:] = [VectorMatch(record.record_id, 1.0, record.metadata)]
    hits = await env.retrieval.retrieve("query", snapshot=env.snapshot(), mode="local")
    assert hits[0].evidence.fact_id == record.metadata["fact_id"]
    assert {source.resource_id for source in hits[0].evidence.sources} == {"a", "c"}
    assert [asdict(chunk) for chunk in env.chunks] == before


@pytest.mark.parametrize("change", ["description", "keywords"])
async def test_global_current_fact_digest_includes_relationship_material(
    graph_env, storage, change
):
    env = graph_env
    env.relations[:] = [env.match(kind=GraphVectorKind.RELATIONSHIP)]
    env.chunks[0].metadata["relationships"][0][change] = (
        ["更新的关键词"] if change == "keywords" else "更新后的关系说明"
    )
    await storage.upsert_chunks([env.chunks[0]])
    assert (
        await env.retrieval.retrieve("query", snapshot=env.snapshot(), mode="global")
        == []
    )
    env.graph.get_relations.assert_not_awaited()


async def test_global_endpoint_source_description_is_current_without_affecting_relation_digest(
    graph_env, storage
):
    env = graph_env
    env.relations[:] = [env.match(kind=GraphVectorKind.RELATIONSHIP)]
    # 关系索引文本使用端点名称和类别说明，不包含端点自己的来源描述。
    env.chunks[0].metadata["entities"][1]["description"] = "新的端点来源描述"
    await storage.upsert_chunks([env.chunks[0]])
    hits = await env.retrieval.retrieve("query", snapshot=env.snapshot(), mode="global")
    assert "新的端点来源描述" in {
        node.description for node in hits[0].evidence.entities
    }


async def test_local_expansion_options_do_not_change_global_relation_scope(graph_env):
    env = graph_env
    env.entities[:] = [env.match(position=1)]
    env.relations[:] = [env.match(kind=GraphVectorKind.RELATIONSHIP)]
    hits = await env.retrieval.retrieve(
        "query",
        snapshot=env.snapshot(),
        neighbor_direction=GraphDirection.OUTGOING,
        neighbor_relation_types=["supplies"],
        neighbor_limit=1,
    )
    assert {edge.relation_type for edge in hits[0].evidence.relationships} == {
        "supplies"
    }
    assert {edge.relation_type for edge in hits[1].evidence.relationships} == {
        "works_at"
    }
    assert env.graph.get_neighbors.call_args.kwargs["limit"] == 1


async def test_old_revision_vector_does_not_fall_back_to_other_source(
    graph_env, publish_read_document, make_read_document
):
    env = graph_env
    env.entities[:] = [env.match()]
    await publish_read_document(make_read_document("a", version=2))
    assert (
        await env.retrieval.retrieve("query", snapshot=env.snapshot(), mode="local")
        == []
    )
    env.graph.get_neighbors.assert_not_awaited()


async def test_retrieval_chunk_seeds_and_traversal_can_read_multiple_written_ontologies(
    graph_env, graph_vector_writers, storage, snapshot_reader
):
    env = graph_env
    foreign = Ontology(
        "research",
        entity_specs={"Topic": EntitySpec("Topic", "主题")},
        relation_specs={"references": RelationSpec("references", "引用主题")},
    )
    chunk = env.chunks[3]
    nodes = [
        foreign.create_node(name=name, category="Topic", source_id=chunk.chunk_id)
        for name in ("主题甲", "主题乙")
    ]
    edge = foreign.create_edge(
        nodes={node.id: node for node in nodes},
        source_id=nodes[0].id,
        target_id=nodes[1].id,
        relation_type="references",
        chunk_id=chunk.chunk_id,
    )
    chunk.metadata.update(
        entities=[asdict(node) for node in nodes], relationships=[asdict(edge)]
    )
    await storage.upsert_chunks([chunk])
    env.graph.nodes = [
        node for node in env.graph.nodes if node.source_id != chunk.chunk_id
    ] + nodes
    env.graph.edges.append(edge)
    entity_writer, relation_writer = graph_vector_writers
    env.embedding.embed.side_effect = lambda *, texts: [[1.0, 0.0] for _ in texts]
    await GraphIndexer(
        ontology=foreign,
        embedding_client=env.embedding,
        entity_writer=entity_writer,
        relationship_writer=relation_writer,
    ).index([chunk])
    record = next(iter(entity_writer.records.values()))
    env.entities[:] = [env.match(), VectorMatch(record.record_id, 0.9, record.metadata)]
    hits = await env.retrieval.retrieve("query", snapshot=env.snapshot(), mode="local")
    assert [hit.evidence.fact_id for hit in hits] == [
        env.facts[0][0][0].id,
        nodes[0].id,
    ]
    assert set(asdict(hits[1].evidence.entities[0])) == {
        "id",
        "source_id",
        "description",
        "relations",
    }
    assert {
        option.relation_type for option in hits[1].evidence.entities[0].relations
    } == {"references"}
    groups = await env.traversal.traverse(
        [env.facts[0][0][0].id, nodes[0].id], snapshot=env.snapshot()
    )
    assert [
        {edge.relation_type for edge in group.relationships} for group in groups
    ] == [{"works_at"}, {"references"}]
    relation_record = next(iter(relation_writer.records.values()))
    env.relations[:] = [
        env.match(kind=GraphVectorKind.RELATIONSHIP),
        VectorMatch(relation_record.record_id, 0.9, relation_record.metadata),
    ]
    # 两路召回和拓扑共用条件；当前事实回查拒绝其他本体，即使旧投影仍返回它。
    filters = MetadataFilters([MetadataFilter("ontology_name", env.ontology.domain)])
    for mode in GraphRetrievalMode:
        hits = await env.retrieval.retrieve(
            "query", snapshot=env.snapshot(), mode=mode, filters=filters
        )
        assert len(hits) == (2 if mode is GraphRetrievalMode.HYBRID else 1)
        assert all(
            {source.resource_id for source in hit.evidence.sources} == {"a", "c"}
            for hit in hits
        )
        assert all(
            edge.relation_type == "works_at"
            for hit in hits
            for edge in hit.evidence.relationships
        )
        reader = (
            env.relation_reader
            if mode is GraphRetrievalMode.GLOBAL
            else env.entity_reader
        )
        assert reader.query.call_args.args[0].filters is filters
    groups = await env.traversal.traverse(
        [nodes[0].id], snapshot=env.snapshot(), filters=filters
    )
    assert groups == []
    chunk_reader = SimpleNamespace(
        query=AsyncMock(
            return_value=[
                VectorMatch(
                    item.chunk_id,
                    1,
                    {
                        "resource_id": item.resource_id,
                        "content_revision": item.content_revision,
                    },
                )
                for item in (env.chunks[0], chunk)
            ]
        )
    )
    ordinary = VectorRetrieval(
        snapshot_reader=snapshot_reader,
        vector_reader=chunk_reader,
        embedding_client=env.embedding,
    )
    chunk_hits = await ordinary.retrieve("query", snapshot=env.snapshot())
    assert [hit.seed_entities[0].id for hit in chunk_hits] == [
        env.facts[0][0][0].id,
        nodes[0].id,
    ]


async def test_changed_extraction_version_invalidates_old_vector_without_query_ontology(
    graph_env, storage
):
    env = graph_env
    env.chunks[0].metadata["graph_version"] = "old"
    env.entities[:] = [env.match()]
    env.chunks[0].metadata["graph_version"] = "new"
    await storage.upsert_chunks([env.chunks[0]])
    assert (
        await env.retrieval.retrieve("query", snapshot=env.snapshot(), mode="local")
        == []
    )
