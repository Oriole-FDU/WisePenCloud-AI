"""通用 provider 的真实装配；底层客户端显式替换，不拉取配置或访问模型。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from common.utils.retrieval import Candidate, RetrievalPipeline
from dependency_injector import providers
from pydantic import ValidationError

from rag.application.graph import GraphEntity, GraphEvidence, PreparedGraphEvidence
from rag.application.publication import DocumentReadyEvent, PublicationAction
from rag.application.retrieval import PreparedChunk
from rag.container import Container, graph_rerank_text
from rag.core.schema import GraphEdge
from rag.storage.graph import GraphVectorKind


def test_shared_config_clients_and_mandatory_indexers(platform_settings):
    container = Container(settings=platform_settings)
    container.mongo_client.override(
        providers.Object(
            {
                "rag-test": {
                    "document_revisions": object(),
                    "doc_chunks": object(),
                    "resource_acls": object(),
                    "resource_publications": object(),
                }
            }
        )
    )
    container.qdrant_client.override(providers.Object(object()))
    container.embedding_openai.override(providers.Object(object()))
    pipeline = container.pipeline()
    assert pipeline.indexers == [container.facts_indexer(), container.vector_indexer()]
    assert pipeline.transformers == []
    stores = [
        container.vector_storage(),
        container.entity_vector_storage(),
        container.relationship_vector_storage(),
    ]
    assert all(store._client is container.qdrant_client() for store in stores)
    assert [store._sparse_vector_name for store in stores] == ["sparse", None, None]
    assert all(store._dense_vector_size == 2 for store in stores)
    assert container.embedding_client()._model == "test-embedding"
    assert container.embedding_client().dimensions == 2
    assert container.vector_indexer()._embedding_client is container.embedding_client()
    assert (
        container.hybrid_retrieval()._embedding_client is container.embedding_client()
    )
    assert container.hybrid_retrieval()._reranker is container.chunk_reranker()
    assert container.rerank_client().model == "test-rerank"
    assert container.rerank_client().api_key == "test-qwen"
    collector = container.garbage_collector()
    assert collector._vector_writers == stores and collector._graph_writers == []
    assert container.graph_storage() is None
    with pytest.raises(RuntimeError, match="ENABLE_GRAPH_STORAGE"):
        container.graph_traversal()
    with pytest.raises(RuntimeError, match="INSTRUCTION"):
        container.instruction_client()


async def test_pipeline_routes_share_live_mapping_and_are_isolated_per_container(
    platform_settings,
    storage,
    publication_store,
):
    from rag.ingestion import IngestionPipeline
    from rag.ingestion.chunkers import MarkdownChunker
    from rag.ingestion.indexers import ChunkFactsIndexer

    first, second = (
        Container(settings=platform_settings),
        Container(settings=platform_settings),
    )
    for container in (first, second):
        container.content_storage.override(providers.Object(storage))
        container.publication_store.override(providers.Object(publication_store))
        container.pipeline.override(providers.Object(object()))
    marker = SimpleNamespace(index=AsyncMock())
    pipeline = IngestionPipeline(
        chunker=MarkdownChunker(),
        transformers=[],
        indexers=[ChunkFactsIndexer(storage), marker],
    )
    # 构造前注册和构造后注册都走同一映射；容器不会复制或全局共享字典。
    first.pipeline_routes()["before"] = pipeline
    publisher = first.publication()
    first.pipeline_routes()["after"] = pipeline
    for type_name in ("before", "after"):
        document = DocumentReadyEvent.model_validate(
            {
                "resourceId": type_name,
                "version": 1,
                "content": "原文",
                "type": type_name,
            }
        ).to_document()
        assert await publisher.publish(document) is PublicationAction.APPLIED
        with pytest.raises(ValueError, match="unregistered document type"):
            await second.publication().publish(document)
    assert marker.index.await_count == 2
    assert first.pipeline_routes() is publisher._pipelines
    assert second.pipeline_routes() == {}


def test_explicit_ontology_factories_and_context_toggle(
    platform_settings, graph_ontology
):
    AppSettings = type(platform_settings)
    values = platform_settings.model_dump() | {
        "ENABLE_CONTEXTUALIZE": True,
        "INSTRUCTION_BASE_URL": "http://localhost:8000/v1",
        "INSTRUCTION_API_KEY": "key",
        "INSTRUCTION_MODEL": "test-instruction",
    }
    container = Container(settings=AppSettings(**values))
    for name in (
        "content_storage",
        "embedding_client",
        "entity_vector_storage",
        "relationship_vector_storage",
        "vector_storage",
    ):
        getattr(container, name).override(providers.Object(object()))
    instruction = object()
    container.instruction_client.override(providers.Object(instruction))
    assert container.pipeline().transformers == [container.contextualizer()]
    extractor = container.graph_extractor(ontology=graph_ontology)
    indexer = container.graph_indexer(ontology=graph_ontology)
    assert extractor._ontology is graph_ontology
    assert indexer._ontology is graph_ontology and indexer._writer is None
    assert (
        container.graph_extractor(ontology=graph_ontology, client=object())
        is not extractor
    )
    graph = object()
    container.graph_storage.override(providers.Object(graph))
    assert container.graph_retrieval()._graph_reader is graph
    assert container.graph_retrieval()._reranker is container.graph_reranker()
    assert container.graph_traversal()._graph_reader is graph
    assert container.garbage_collector()._graph_writers == [graph]


@pytest.mark.parametrize(
    "change",
    [
        {"QWEN_API_KEY": ""},
        {"QWEN_RERANKER_MODEL": None},
        {"ENABLE_CONTEXTUALIZE": True},
        {"INSTRUCTION_MODEL": "partial"},
        {"ENABLE_GRAPH_STORAGE": True},
        {"KAFKA_ENABLED": True},
        {"GC_BATCH_SIZE": 0},
        {"GC_INTERVAL_SECONDS": float("inf")},
        {"EMBEDDING_DIMENSIONS": 0},
        {"VECTOR_COLLECTION": "graph_entity_source_vectors_v4"},
        {"KAFKA_RAG_PUBLICATION_GROUP_ID": "wispen-rag-v4-acl-recalc-group"},
    ],
)
def test_capability_configuration_fails_before_connections(platform_settings, change):
    AppSettings = type(platform_settings)
    with pytest.raises(ValidationError):
        AppSettings(**(platform_settings.model_dump() | change))


def test_no_gc_or_rerank_switch(platform_settings):
    AppSettings = type(platform_settings)
    assert "ENABLE_GC" not in AppSettings.model_fields
    assert "ENABLE_RERANK" not in AppSettings.model_fields
    assert (
        not platform_settings.ENABLE_CONTEXTUALIZE
        and not platform_settings.KAFKA_ENABLED
    )


async def test_platform_rerank_materials_empty_and_failure_fallback(platform_settings):
    container = Container(settings=platform_settings)
    model = SimpleNamespace(
        model="test-rerank", rerank=AsyncMock(side_effect=OSError("model down"))
    )
    container.rerank_client.override(providers.Object(model))
    chunk = SimpleNamespace(
        raw_text="完整正文" * 500, metadata={"contextual_context": "上下文"}
    )
    evidence = GraphEvidence(
        GraphVectorKind.ENTITY,
        "work:Person:张三",
        [GraphEntity("work:Person:张三", "chunk", "独立描述", [])],
        [GraphEdge("p", "c", "works_at", "任职描述", ["任职"], ["chunk"])],
        [],
    )
    assert "work:Person:张三" in graph_rerank_text(
        PreparedGraphEvidence(evidence, None)
    )
    assert "任职描述" in graph_rerank_text(PreparedGraphEvidence(evidence, None))
    for payload, reranker in [
        (PreparedChunk(chunk, None), container.chunk_reranker()),
        (PreparedGraphEvidence(evidence, None), container.graph_reranker()),
    ]:
        candidates = [
            Candidate("first", payload, 1, 0.9),
            Candidate("second", payload, 2, 0.8),
        ]
        retriever = SimpleNamespace(retrieve=AsyncMock(return_value=[]))
        pipeline = RetrievalPipeline(retriever=retriever, reranker=reranker)
        assert await pipeline.run(query="q", top_k=2) == []
        model.rerank.assert_not_awaited()
        retriever.retrieve.return_value = candidates
        result = await pipeline.run(query="q", top_k=2)
        assert [item.candidate_id for item in result] == ["first", "second"]
        material = model.rerank.call_args.kwargs["documents"][0]
        assert material == (
            chunk.raw_text
            if isinstance(payload, PreparedChunk)
            else graph_rerank_text(payload)
        )
        model.rerank.reset_mock()
