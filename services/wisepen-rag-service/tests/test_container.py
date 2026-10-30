"""通用 provider 的真实装配；底层客户端显式替换，不拉取配置或访问模型。"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from dependency_injector import providers
from pydantic import ValidationError

from common.utils.retrieval import Candidate, RetrievalPipeline
from rag.application.publication import DocumentReadyEvent
from rag.domain.models.publication import PublicationAction
from rag.application.chunk import PreparedChunk
from rag.container import Container


async def test_message_clients_use_configured_topics_groups_and_poll_interval(
        platform_settings, monkeypatch
):
    AppSettings = type(platform_settings)
    settings = AppSettings(
        **platform_settings.model_dump()
        | {"KAFKA_ENABLED": True, "KAFKA_BOOTSTRAP_SERVERS": "localhost:9092"}
    )
    container = Container(settings=settings)
    connections = []

    def create_consumer(topic, **kwargs):
        connections.append((topic, kwargs))
        return SimpleNamespace(
            start=AsyncMock(),
            stop=AsyncMock(),
            getone=AsyncMock(side_effect=asyncio.CancelledError),
            commit=AsyncMock(),
        )

    producer = SimpleNamespace(start=AsyncMock(), stop=AsyncMock())
    monkeypatch.setattr(
        "rag.core.kafka.AIOKafkaConsumer", Mock(side_effect=create_consumer)
    )
    monkeypatch.setattr(
        "rag.core.kafka.AIOKafkaProducer", Mock(return_value=producer)
    )

    for provider in (
        container.acl_message_consumer,
        container.publication_content_consumer,
        container.publication_destroy_consumer,
    ):
        with pytest.raises(asyncio.CancelledError):
            await provider().run(AsyncMock())

    assert [
        (
            topic,
            kwargs["bootstrap_servers"],
            kwargs["group_id"],
            kwargs["max_poll_interval_ms"],
        )
        for topic, kwargs in connections
    ] == [
        (
            settings.KAFKA_RESOURCE_ACL_RECALC_TOPIC,
            settings.KAFKA_BOOTSTRAP_SERVERS,
            settings.KAFKA_RAG_ACL_RECALC_GROUP_ID,
            settings.KAFKA_MAX_POLL_INTERVAL_MS,
        ),
        (
            settings.KAFKA_DOCUMENT_READY_TOPIC,
            settings.KAFKA_BOOTSTRAP_SERVERS,
            settings.KAFKA_RAG_PUBLICATION_GROUP_ID,
            settings.KAFKA_MAX_POLL_INTERVAL_MS,
        ),
        (
            settings.KAFKA_RESOURCE_PHYSICAL_DESTROY_TOPIC,
            settings.KAFKA_BOOTSTRAP_SERVERS,
            settings.KAFKA_RAG_PUBLICATION_GROUP_ID,
            settings.KAFKA_MAX_POLL_INTERVAL_MS,
        ),
    ]


async def test_pipeline_routes_share_live_mapping_and_are_isolated_per_container(
        platform_settings,
        storage,
        publication_store,
):
    from rag.application.publication.ingestion import IngestionPipeline
    from rag.application.publication.ingestion import MarkdownChunker
    from rag.application.publication.ingestion import ChunkFactsIndexer

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
    assert second.pipeline_routes() == {}


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
        {"KAFKA_RAG_PUBLICATION_GROUP_ID": "wisepen-rag-v4-acl-recalc-group"},
    ],
)
def test_capability_configuration_fails_before_connections(platform_settings, change):
    AppSettings = type(platform_settings)
    with pytest.raises(ValidationError):
        AppSettings(**(platform_settings.model_dump() | change))


@pytest.mark.parametrize(
    "enabled,pipeline_name,expects_context",
    [
        (False, "pipeline", False),
        (True, "pipeline", True),
        (True, "pipeline_without_context", False),
        (True, "pipeline_with_context", True),
    ],
)
async def test_configured_pipeline_persists_facts_and_vectors(
        platform_settings, storage, document, enabled, pipeline_name, expects_context
):
    AppSettings = type(platform_settings)
    values = platform_settings.model_dump() | {"ENABLE_CONTEXTUALIZE": enabled}
    if enabled:
        values |= {
            "INSTRUCTION_BASE_URL": "http://localhost:8000/v1",
            "INSTRUCTION_API_KEY": "key",
            "INSTRUCTION_MODEL": "test-instruction",
        }
    container = Container(settings=AppSettings(**values))
    instruction = SimpleNamespace(
        model="test-instruction", complete=AsyncMock(return_value="文档上下文")
    )
    embedding = SimpleNamespace(
        embed=AsyncMock(side_effect=lambda *, texts: [[1.0, 0.0] for _ in texts])
    )
    vectors = SimpleNamespace(upsert=AsyncMock())
    container.content_storage.override(providers.Object(storage))
    container.embedding_client.override(providers.Object(embedding))
    container.vector_storage.override(providers.Object(vectors))
    if enabled:
        container.instruction_client.override(providers.Object(instruction))

    # 运行真实装配链，验证配置对最终事实和向量的影响。
    chunks = await getattr(container, pipeline_name)().run(document)
    persisted = await storage.get_revision_chunks(
        resource_id=document.resource_id, content_revision=document.content_revision
    )
    assert persisted == chunks
    assert chunks
    assert all(
        chunk.metadata.get("contextual_context")
        == ("文档上下文" if expects_context else None)
        for chunk in persisted
    )
    records = [
        record for call in vectors.upsert.await_args_list for record in call.args[0]
    ]
    assert [record.record_id for record in records] == [
        chunk.chunk_id for chunk in chunks
    ]
    assert [record.text for record in records] == [
        chunk.retrieval_text for chunk in chunks
    ]


async def test_platform_rerank_materials_empty_and_failure_fallback(platform_settings):
    container = Container(settings=platform_settings)
    model = SimpleNamespace(
        model="test-rerank", rerank=AsyncMock(side_effect=OSError("model down"))
    )
    container.rerank_client.override(providers.Object(model))
    chunk = SimpleNamespace(
        raw_text="完整正文" * 500, metadata={"contextual_context": "上下文"}
    )
    payload = PreparedChunk(chunk, None)
    candidates = [
        Candidate("first", payload, 1, 0.9),
        Candidate("second", payload, 2, 0.8),
    ]
    retriever = SimpleNamespace(retrieve=AsyncMock(return_value=[]))
    pipeline = RetrievalPipeline(
        retriever=retriever, reranker=container.chunk_reranker()
    )
    assert await pipeline.run(query="q", top_k=2) == []
    model.rerank.assert_not_awaited()
    retriever.retrieve.return_value = candidates
    result = await pipeline.run(query="q", top_k=2)
    assert [item.candidate_id for item in result] == ["first", "second"]
    material = model.rerank.call_args.kwargs["documents"][0]
    assert material == chunk.raw_text
