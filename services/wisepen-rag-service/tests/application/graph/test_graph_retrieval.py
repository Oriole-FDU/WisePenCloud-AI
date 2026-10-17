"""图召回先授权，再收拢共享拓扑与来源；只有完整 Chunk 原文参与精排。"""

import asyncio
from dataclasses import asdict, replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from common.utils.retrieval import ModelReranker
from pydantic import TypeAdapter

from rag.application.acl.models import ResourceAcl
from rag.application.graph import (
    GraphRetrieval,
    GraphRetrievalResult,
    GraphTraversalResult,
)
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


@pytest.mark.parametrize("mode", ["local", "global", "hybrid", "traversal"])
async def test_grouped_sources_keep_descriptions_keywords_and_metadata_authorized(
    graph_env, storage, read_acls, mode
):
    env = graph_env
    first, second = env.chunks[0], env.chunks[2]
    second.metadata["entities"][0]["description"] = None
    second.metadata["relationships"][0].update(
        # 相同描述仍是两个来源；其他来源字段不能被首份事实覆盖。
        description=first.metadata["relationships"][0]["description"],
        keywords=["项目管理"],
        metadata={"audit": {"extractor": "second"}},
    )
    await storage.upsert_chunks([second])
    env.entities[:] = [env.match(), env.match()]
    env.relations[:] = [env.match(kind=GraphVectorKind.RELATIONSHIP)]
    person_id = env.facts[0][0][0].id

    async def retrieve():
        if mode == "traversal":
            return await env.traversal.traverse(
                [person_id, person_id], snapshot=env.snapshot()
            )
        return await env.retrieval.retrieve("query", snapshot=env.snapshot(), mode=mode)

    result = await retrieve()
    person = next(node for node in result.entities if node.id == person_id)
    assert [source.chunk_id for source in person.sources] == [
        first.chunk_id,
        second.chunk_id,
    ]
    assert [source.description for source in person.sources] == ["a-张三", None]
    assert len(result.relationships) == 1
    relationship = result.relationships[0]
    assert [source.chunk_id for source in relationship.sources] == [
        first.chunk_id,
        second.chunk_id,
    ]
    assert [source.description for source in relationship.sources] == ["a-任职"] * 2
    assert relationship.sources[0].keywords == env.facts[0][1][0].keywords
    assert relationship.sources[0].metadata == {}
    assert relationship.sources[1].keywords == ["项目管理"]
    assert relationship.sources[1].metadata == {"audit": {"extractor": "second"}}
    # dataclass 与对外 JSON 都显式包含逻辑 ID，且不再暴露完整核心实体字段。
    serialized = TypeAdapter(type(result)).dump_python(result, mode="json")
    assert set(serialized["entities"][0]) == {"id", "sources"}
    assert all("sources" in edge for edge in serialized["relationships"])
    assert second.metadata["relationships"][0]["metadata"] == {
        "audit": {"extractor": "second"}
    }

    # 同一个逻辑对象仍可见时，隐藏的描述、关键词和 metadata 都必须退出来源列表。
    read_acls["c"] = ResourceAcl("c", 1, "other")
    visible = await retrieve()
    assert len(visible.relationships) == 1
    assert [source.chunk_id for source in visible.relationships[0].sources] == [
        first.chunk_id
    ]
    assert all(
        source.chunk_id != second.chunk_id
        for node in visible.entities
        for source in node.sources
    )


@pytest.mark.parametrize("mode", list(GraphRetrievalMode))
async def test_modes_group_sources_once_and_keep_full_material(graph_env, mode):
    env = graph_env
    env.entities[:] = [env.match(0, score=0.9), env.match(2, score=0.8)]
    env.relations[:] = [env.match(0, kind=GraphVectorKind.RELATIONSHIP)]
    result = await env.retrieval.retrieve(" query ", snapshot=env.snapshot(), mode=mode)
    assert {chunk.resource_id for chunk in result.chunks} == (
        {"a", "c"} if mode is GraphRetrievalMode.LOCAL else {"a", "b", "c", "d"}
    )
    assert {
        source.description for edge in result.relationships for source in edge.sources
    } == {"a-任职", "c-任职"}
    assert {
        source.description for node in result.entities for source in node.sources
    } == (
        {"a-张三", "c-张三"}
        if mode is GraphRetrievalMode.LOCAL
        else {"a-张三", "c-张三", "a-甲公司", "b-甲公司", "c-甲公司", "d-无关公司描述"}
    )
    assert [hit.rank for hit in result.chunks] == list(range(1, len(result.chunks) + 1))
    assert len({node.id for node in result.entities}) == len(result.entities)
    assert len(result.relationships) == 1
    assert len(result.relationships[0].sources) == 2
    assert set(asdict(result)) == {"entities", "relationships", "chunks"}
    assert all(
        "entities" not in hit.metadata and "relationships" not in hit.metadata
        for hit in result.chunks
    )
    env.embedding.embed.assert_awaited_once_with(texts=["query"])
    assert env.entity_reader.query.await_count == (
        mode is not GraphRetrievalMode.GLOBAL
    )
    assert env.relation_reader.query.await_count == (
        mode is not GraphRetrievalMode.LOCAL
    )
    limited_calls = [
        call
        for call in env.graph.get_neighbors.call_args_list
        if call.kwargs["limit"] is not None
    ]
    assert len(limited_calls) == (mode is not GraphRetrievalMode.GLOBAL)


async def test_shared_context_adds_global_endpoint_sources_without_their_relations(
    graph_env,
):
    env = graph_env
    env.relations[:] = [env.match(kind=GraphVectorKind.RELATIONSHIP)]
    result = await env.retrieval.retrieve(
        "query", snapshot=env.snapshot(), mode="global"
    )
    assert {hit.resource_id for hit in result.chunks} == {"a", "b", "c", "d"}
    assert "d-无关公司描述" in {
        source.description for node in result.entities for source in node.sources
    }
    assert {edge.relation_type for edge in result.relationships} == {"works_at"}
    assert all(
        call.kwargs["limit"] is None for call in env.graph.get_neighbors.call_args_list
    )


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
    assert {source.resource_id for source in hits.chunks} == {"c"}
    assert all(
        source.chunk_id == env.chunks[2].chunk_id
        for node in hits.entities
        for source in node.sources
    )
    # 只有不可见候选时，即使同逻辑 ID 在其他来源可见，也不能反向扩成种子。
    env.entities[:] = [env.match(0)]
    env.graph.get_neighbors.reset_mock()
    assert (
        await env.retrieval.retrieve("query", snapshot=env.snapshot(), mode="local")
        == GraphRetrievalResult()
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
        == GraphRetrievalResult()
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
        == GraphRetrievalResult()
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
        == GraphRetrievalResult()
    )
    # 通过未变化的公司种子验证拓扑材料始终采用当前来源事实。
    env.relations[:] = [env.match(kind=GraphVectorKind.RELATIONSHIP)]
    hits = await env.retrieval.retrieve("query", snapshot=env.snapshot(), mode="global")
    descriptions = {
        source.description for node in hits.entities for source in node.sources
    }
    assert "更新后的人员描述" in descriptions
    assert "a-张三" not in descriptions


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
            == GraphRetrievalResult()
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
    assert {
        source.chunk_id for edge in hits.relationships for source in edge.sources
    } == {env.chunks[2].chunk_id}
    assert env.chunks[0].chunk_id in {
        source.chunk_id for node in hits.entities for source in node.sources
    }


async def test_missing_topology_preserves_authorized_vector_seed(graph_env):
    env = graph_env
    env.entities[:] = [env.match()]
    env.graph.nodes = []
    env.graph.edges = []
    hits = await env.retrieval.retrieve("query", snapshot=env.snapshot(), mode="local")
    assert [node.id for node in hits.entities] == [env.facts[0][0][0].id]
    assert hits.relationships == []


async def test_reranking_uses_unique_full_chunks_and_top_k_only_limits_chunks(
    graph_env,
):
    env = graph_env
    env.entities[:] = [env.match(0), env.match(2), env.match(0, position=1)]
    client = SimpleNamespace(
        model="rerank",
        rerank=AsyncMock(
            return_value=[
                {"index": 3, "relevance_score": 0.95},
                {"index": 1, "relevance_score": 0.8},
                {"index": 0, "relevance_score": 0.7},
                {"index": 2, "relevance_score": 0.6},
            ]
        ),
    )
    usecase = GraphRetrieval(
        **env.dependencies,
        reranker=ModelReranker(
            client=client, text_getter=lambda payload: payload.chunk.raw_text
        ),
    )
    result = await usecase.retrieve(
        "query", snapshot=env.snapshot(), mode="local", top_k=2, focus=" focus "
    )
    documents = client.rerank.call_args.kwargs["documents"]
    assert len(documents) == len(set(documents)) == 4
    assert set(documents) == {chunk.raw_text for chunk in env.chunks}
    assert client.rerank.call_args.kwargs["query"] == "focus"
    assert [hit.text for hit in result.chunks] == [documents[3], documents[1]]
    assert [hit.score for hit in result.chunks] == [0.95, 0.8]
    assert len(result.entities) == 2 and len(result.relationships) == 2
    assert sum(len(node.sources) for node in result.entities) == 6
    assert sum(len(edge.sources) for edge in result.relationships) == 3
    assert env.entity_reader.query.call_args.args[0].include_embedding is False


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
            len(
                (
                    await usecase.retrieve(
                        "query", snapshot=env.snapshot(), mode="local"
                    )
                ).chunks
            )
            == 2
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
        == GraphRetrievalResult()
    )


async def test_concurrent_calls_keep_request_state_separate(graph_env):
    env = graph_env
    env.entities[:] = [env.match()]
    allowed, denied = await asyncio.gather(
        env.retrieval.retrieve("allow", snapshot=env.snapshot(), mode="local"),
        env.retrieval.retrieve("deny", snapshot=env.snapshot("other"), mode="local"),
    )
    assert len(allowed.chunks) == 2 and denied == GraphRetrievalResult()
    assert env.graph.get_neighbors.await_count == 2


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
    assert {source.resource_id for source in hits.chunks} == {"c"}
    assert env.entity_reader.query.call_args.args[0].filters is filters
    assert env.graph.get_nodes.call_args.kwargs["filters"] is filters
    assert env.graph.get_neighbors.call_args_list[0].kwargs["filters"] is filters


@pytest.mark.parametrize(
    "kwargs",
    [
        {"query": " "},
        {"top_k": 0},
        {"candidate_limit": 0},
        {"mode": "mix"},
        {"entity_top_k": 0},
        {"relationship_top_k": 0},
        {"mode": "local", "entity_top_k": 3},
        {"neighbor_direction": "bad"},
    ],
)
async def test_common_and_enum_validation_fails_before_io(graph_env, kwargs):
    # 只验证 Common 与枚举的现有边界；应用预算的取值约束由调用方负责。
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
    assert (
        await usecase.retrieve("query", snapshot=env.snapshot(), mode="local")
        == GraphRetrievalResult()
    )
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
    assert hits.entities[0].id == record.metadata["fact_id"]
    assert {source.resource_id for source in hits.chunks} == {"a", "c"}
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
        == GraphRetrievalResult()
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
        source.description for node in hits.entities for source in node.sources
    }


async def test_local_expansion_options_do_not_change_global_relation_scope(graph_env):
    env = graph_env
    env.entities[:] = [env.match(position=1)]
    env.relations[:] = [env.match(kind=GraphVectorKind.RELATIONSHIP)]
    result = await env.retrieval.retrieve(
        "query",
        snapshot=env.snapshot(),
        neighbor_direction=GraphDirection.OUTGOING,
        neighbor_relation_types=["supplies"],
        neighbor_limit=1,
    )
    assert {edge.relation_type for edge in result.relationships} == {
        "supplies",
        "works_at",
    }
    assert env.graph.get_neighbors.call_args_list[0].kwargs["limit"] == 1


async def test_old_revision_vector_does_not_fall_back_to_other_source(
    graph_env, publish_read_document, make_read_document
):
    env = graph_env
    env.entities[:] = [env.match()]
    await publish_read_document(make_read_document("a", version=2))
    assert (
        await env.retrieval.retrieve("query", snapshot=env.snapshot(), mode="local")
        == GraphRetrievalResult()
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
    assert list(dict.fromkeys(node.id for node in hits.entities)) == [
        env.facts[0][0][0].id,
        nodes[0].id,
    ]
    foreign_node = next(node for node in hits.entities if node.id == nodes[0].id)
    assert set(asdict(foreign_node)) == {"id", "sources"}
    assert [source.chunk_id for source in foreign_node.sources] == [chunk.chunk_id]
    groups = await env.traversal.traverse(
        [env.facts[0][0][0].id, nodes[0].id], snapshot=env.snapshot()
    )
    assert {edge.relation_type for edge in groups.relationships} == {
        "works_at",
        "references",
    }
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
        assert {source.resource_id for source in hits.chunks} == (
            {"a", "c"} if mode is GraphRetrievalMode.LOCAL else {"a", "b", "c"}
        )
        assert all(edge.relation_type == "works_at" for edge in hits.relationships)
        reader = (
            env.relation_reader
            if mode is GraphRetrievalMode.GLOBAL
            else env.entity_reader
        )
        assert reader.query.call_args.args[0].filters is filters
    groups = await env.traversal.traverse(
        [nodes[0].id], snapshot=env.snapshot(), filters=filters
    )
    assert groups == GraphTraversalResult()
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
        == GraphRetrievalResult()
    )
