"""事件合同、发布闭环、DLQ 与消息确认；延迟预算由 Kafka 后端测试独立验证。"""

import asyncio
import json
import time
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from aiokafka import TopicPartition
from common.utils.markdown import MarkdownChunker
from pydantic import ValidationError

from rag.application.publication import (
    DocumentReadyEvent,
    PublicationEventConsumer,
    ResourceDestroyedEvent,
)
from rag.backends.kafka import DeliveryHeader, RetryPolicy
from rag.core.schema import ContentRevision
from rag.core.schema.content import rag_section_id

DOCUMENT_TOPIC = "wisepen-document-ready-topic"
DESTROY_TOPIC = "wisepen-resource-physical-destroy-topic"


def record(payload, *, topic=DOCUMENT_TOPIC, partition=2, offset=10):
    return SimpleNamespace(
        topic=topic,
        partition=partition,
        offset=offset,
        value=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        key=b"resource",
        headers=[],
        timestamp=int(time.time() * 1000),
    )


def content(*, version=1, text="# 标题\n\n  中文😀。\n", resource_id="resource"):
    return {"resourceId": resource_id, "version": version, "content": text}


@pytest.fixture
def kafka(monkeypatch):
    async def wait_for_message():
        await asyncio.Event().wait()

    client = SimpleNamespace(
        start=AsyncMock(), stop=AsyncMock(), getone=AsyncMock(), commit=AsyncMock()
    )
    destroy = SimpleNamespace(
        start=AsyncMock(),
        stop=AsyncMock(),
        getone=AsyncMock(side_effect=wait_for_message),
        commit=AsyncMock(),
    )
    producer = SimpleNamespace(
        start=AsyncMock(), stop=AsyncMock(), send_and_wait=AsyncMock()
    )
    factory = Mock(return_value=client)
    factory.destroy_client = destroy
    factory.producer = producer
    factory.side_effect = lambda topic, **kwargs: (
        factory.return_value if topic == DOCUMENT_TOPIC else factory.destroy_client
    )
    monkeypatch.setattr("rag.backends.kafka.consumer.AIOKafkaConsumer", factory)
    monkeypatch.setattr(
        "rag.backends.kafka.consumer.AIOKafkaProducer", Mock(return_value=producer)
    )
    return client, factory


@pytest.fixture
def consumer(publication):
    # 应用测试关闭等待阶段，集中验证业务与确认时序；生产默认仍为五次重试。
    policy = RetryPolicy(backoff_seconds=[], delayed_retries=[])
    return PublicationEventConsumer(
        publisher=publication,
        bootstrap_servers="localhost:9092",
        group_id="rag-test",
        max_poll_interval_ms=900_000,
        content_retry_policy=policy,
        destroy_retry_policy=policy,
    )


async def test_commit_happens_after_applied_and_only_for_current_partition(
    consumer, kafka, publication_store, storage, projection
):
    client, factory = kafka
    client.getone.side_effect = [record(content()), asyncio.CancelledError()]

    async def commit(offsets):
        state = (await publication_store.get_states(["resource"]))[0]
        assert state.applied_revision is not None and state.staged_revision is None
        assert state.applied_revision.content_revision == (
            DocumentReadyEvent.model_validate(content()).to_document().content_revision
        )
        documents = await storage.get_documents(
            [("resource", state.applied_revision.content_revision)]
        )
        assert (
            documents[0].text == content()["content"]
            and documents[0].structure.sections
        )
        projection.index.assert_awaited_once()
        assert offsets == {TopicPartition(DOCUMENT_TOPIC, 2): 11}

    client.commit.side_effect = commit
    with pytest.raises(asyncio.CancelledError):
        await consumer.run()
    factory.assert_any_call(
        DOCUMENT_TOPIC,
        bootstrap_servers="localhost:9092",
        group_id="rag-test",
        enable_auto_commit=False,
        auto_offset_reset="earliest",
        max_poll_interval_ms=900_000,
    )
    client.stop.assert_awaited_once()
    factory.destroy_client.stop.assert_awaited_once()


async def test_platform_consumer_routes_type_and_preserves_metadata(
    consumer,
    kafka,
    pipeline_routes,
    storage,
    publication_store,
):
    from rag.ingestion import IngestionPipeline
    from rag.ingestion.chunkers import MarkdownChunker as RagMarkdownChunker
    from rag.ingestion.indexers import ChunkFactsIndexer

    marker = SimpleNamespace(index=AsyncMock())
    pipeline_routes["history"] = IngestionPipeline(
        chunker=RagMarkdownChunker(),
        transformers=[],
        indexers=[ChunkFactsIndexer(storage), marker],
    )
    payload = content() | {"type": "history", "metadata": {"year": 1850}}
    client, factory = kafka
    client.getone.side_effect = [record(payload), asyncio.CancelledError()]
    with pytest.raises(asyncio.CancelledError):
        await consumer.run()
    state = (await publication_store.get_states(["resource"]))[0]
    document = (
        await storage.get_documents(
            [("resource", state.applied_revision.content_revision)]
        )
    )[0]
    assert document.type == "history" and document.metadata == {"year": 1850}
    marker.index.assert_awaited_once()
    client.commit.assert_awaited_once()
    factory.producer.send_and_wait.assert_not_awaited()


async def test_unknown_type_uses_existing_permanent_error_dlq_contract(
    consumer,
    kafka,
    collections,
    publication_collection,
):
    client, factory = kafka
    client.getone.side_effect = [
        record(content() | {"type": "unregistered"}),
        asyncio.CancelledError(),
    ]
    with pytest.raises(asyncio.CancelledError):
        await consumer.run()
    assert (
        dict(factory.producer.send_and_wait.call_args.kwargs["headers"])[
            DeliveryHeader.RETRY_COUNT
        ]
        == b"0"
    )
    client.commit.assert_awaited_once()
    publication_collection.find_one.assert_not_awaited()
    publication_collection.update_one.assert_not_awaited()
    for collection in collections:
        collection.bulk_write.assert_not_awaited()


@pytest.mark.parametrize("skip", ["duplicate", "stale", "destroyed"])
async def test_explicit_skips_are_committed_without_rebuilding(
    consumer, kafka, publication, projection, skip
):
    await publication.publish(
        DocumentReadyEvent.model_validate(content(version=2)).to_document()
    )
    if skip == "destroyed":
        await publication.destroy_resources(["resource"])
    projection.index.reset_mock()
    client, factory = kafka
    client.getone.side_effect = [
        record(content(version=1 if skip == "stale" else 2)),
        asyncio.CancelledError(),
    ]
    with pytest.raises(asyncio.CancelledError):
        await consumer.run()
    client.commit.assert_awaited_once_with({TopicPartition(DOCUMENT_TOPIC, 2): 11})
    projection.index.assert_not_awaited()
    factory.producer.send_and_wait.assert_not_awaited()


@pytest.mark.parametrize(
    "bad",
    [
        b"{",
        b"null",
        b"[]",
        b'"json string"',
        b'{"resourceId":"resource","version":true,"content":"x"}',
    ],
)
async def test_bad_payload_goes_to_dlq_before_commit_without_business_io(
    consumer, kafka, collections, publication_collection, bad
):
    message = record(content())
    message.value = bad
    client, factory = kafka
    client.getone.side_effect = [message, asyncio.CancelledError()]

    async def send(topic, **kwargs):
        assert topic == f"{DOCUMENT_TOPIC}.DLT" and kwargs["value"] == bad
        assert dict(kwargs["headers"])[DeliveryHeader.RETRY_COUNT] == b"0"
        client.commit.assert_not_awaited()

    factory.producer.send_and_wait.side_effect = send
    with pytest.raises(asyncio.CancelledError):
        await consumer.run()
    client.commit.assert_awaited_once_with({TopicPartition(DOCUMENT_TOPIC, 2): 11})
    publication_collection.find_one.assert_not_awaited()
    for collection in collections:
        collection.bulk_write.assert_not_awaited()


async def test_same_version_conflict_goes_directly_to_dlq_without_rebuild(
    consumer, kafka, publication, projection
):
    await publication.publish(
        DocumentReadyEvent.model_validate(content()).to_document()
    )
    projection.index.reset_mock()
    client, factory = kafka
    client.getone.side_effect = [
        record(content(text="冲突正文")),
        asyncio.CancelledError(),
    ]
    with pytest.raises(asyncio.CancelledError):
        await consumer.run()
    headers = dict(factory.producer.send_and_wait.call_args.kwargs["headers"])
    assert headers[DeliveryHeader.RETRY_COUNT] == b"0"
    assert headers[DeliveryHeader.EXCEPTION_TYPE].endswith(b"ContentConflictError")
    projection.index.assert_not_awaited()


async def test_exhausted_projection_failure_keeps_old_applied_and_facts_before_dlq_ack(
    consumer, kafka, publication, projection, publication_store, storage
):
    first = DocumentReadyEvent.model_validate(content()).to_document()
    await publication.publish(first)
    client, factory = kafka
    candidate = record(content(version=2))
    client.getone.side_effect = [candidate, asyncio.CancelledError()]
    projection.index.side_effect = RuntimeError("required projection failed")

    async def send(topic, **kwargs):
        assert topic == f"{DOCUMENT_TOPIC}.DLT"
        state = (await publication_store.get_states(["resource"]))[0]
        assert state.applied_revision.content_revision == first.content_revision
        assert state.staged_revision.document_version == 2
        assert await storage.get_revision_chunks(
            resource_id="resource",
            content_revision=state.staged_revision.content_revision,
        )
        client.commit.assert_not_awaited()

    factory.producer.send_and_wait.side_effect = send
    with pytest.raises(asyncio.CancelledError):
        await consumer.run()
    client.commit.assert_awaited_once()


async def test_commit_failure_propagates_then_replay_skips_already_applied_build(
    consumer, kafka, projection, publication_store
):
    client, factory = kafka
    message = record(content())
    client.getone.side_effect = [message, record(content(version=2), offset=11)]
    error = RuntimeError("offset commit failed")
    client.commit.side_effect = error
    with pytest.raises(RuntimeError) as caught:
        await consumer.run()
    assert caught.value is error
    client.getone.assert_awaited_once()
    assert (await publication_store.get_states(["resource"]))[0].applied_revision
    projection.index.reset_mock()
    replay = SimpleNamespace(
        start=AsyncMock(),
        stop=AsyncMock(),
        getone=AsyncMock(side_effect=[message, asyncio.CancelledError()]),
        commit=AsyncMock(),
    )
    factory.return_value = replay
    with pytest.raises(asyncio.CancelledError):
        await consumer.run()
    projection.index.assert_not_awaited()
    replay.commit.assert_awaited_once_with({TopicPartition(DOCUMENT_TOPIC, 2): 11})


async def test_failed_dlq_send_stops_both_loops_and_leaves_source_uncommitted(
    consumer, kafka
):
    client, factory = kafka
    client.getone.return_value = record({"bad": True})
    error = RuntimeError("DLQ broker write failed")
    factory.producer.send_and_wait.side_effect = error
    with pytest.raises(RuntimeError) as caught:
        await consumer.run()
    assert caught.value is error
    client.commit.assert_not_awaited()
    client.stop.assert_awaited_once()
    factory.destroy_client.stop.assert_awaited_once()


async def test_destroy_loop_invalidates_content_during_projection_and_both_commit(
    consumer, kafka, projection, publication_store
):
    client, factory = kafka
    client.getone.side_effect = [record(content()), asyncio.CancelledError()]
    entered, destroyed = asyncio.Event(), asyncio.Event()

    async def project(chunks):
        entered.set()
        await destroyed.wait()

    async def destroy_message():
        await entered.wait()
        if factory.destroy_client.getone.await_count == 1:
            return record(
                {"typedResourceIds": {"DOCUMENT": ["resource"]}}, topic=DESTROY_TOPIC
            )
        await asyncio.Event().wait()

    async def destroy_commit(offsets):
        state = (await publication_store.get_states(["resource"]))[0]
        assert state.destroyed and state.applied_revision is None
        destroyed.set()

    projection.index.side_effect = project
    factory.destroy_client.getone.side_effect = destroy_message
    factory.destroy_client.commit.side_effect = destroy_commit
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(consumer.run(), timeout=2)
    client.commit.assert_awaited_once_with({TopicPartition(DOCUMENT_TOPIC, 2): 11})
    factory.producer.send_and_wait.assert_not_awaited()


async def test_cancel_during_projection_keeps_uncommitted_staged_facts(
    consumer, kafka, projection, publication_store
):
    client, _ = kafka
    client.getone.return_value = record(content())
    entered = asyncio.Event()

    async def project(chunks):
        entered.set()
        await asyncio.Event().wait()

    projection.index.side_effect = project
    task = asyncio.create_task(consumer.run())
    try:
        await asyncio.wait_for(entered.wait(), timeout=2)
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    client.commit.assert_not_awaited()
    state = (await publication_store.get_states(["resource"]))[0]
    assert state.staged_revision is not None and state.applied_revision is None


async def test_temporarily_incomplete_facts_are_retried_until_real_apply(
    kafka, publication, storage, projection, publication_store
):
    original_read = storage.get_documents
    reads = 0

    async def read(keys):
        nonlocal reads
        reads += 1
        if reads == 1:
            return []
        return await original_read(keys)

    storage.get_documents = read
    consumer = PublicationEventConsumer(
        publisher=publication,
        bootstrap_servers="localhost:9092",
        group_id="rag-test",
        content_retry_policy=RetryPolicy(backoff_seconds=[0]),
        destroy_retry_policy=RetryPolicy(backoff_seconds=[]),
    )
    client, factory = kafka
    client.getone.side_effect = [record(content()), asyncio.CancelledError()]
    with pytest.raises(asyncio.CancelledError):
        await consumer.run()
    assert projection.index.await_count == 2
    state = (await publication_store.get_states(["resource"]))[0]
    assert state.applied_revision is not None and state.staged_revision is None
    factory.producer.send_and_wait.assert_not_awaited()
    client.commit.assert_awaited_once()


# --- 事件输入合同：拒绝字段漂移，完整保留正文与 Common 结构 ---


@pytest.mark.parametrize("text", ["", " \n\t", "# 中文😀\n\n  正文。\n"])
def test_content_event_builds_canonical_document_without_changing_text(text):
    event = DocumentReadyEvent.model_validate(
        {"resourceId": "resource", "version": 7, "content": text, "future": True}
    )
    document = event.to_document()
    revision = ContentRevision.create(
        resource_id="resource", document_version=7, text=text
    )
    parsed = MarkdownChunker().chunk(text)
    assert document.resource_id == "resource" and document.document_version == 7
    assert document.content_revision == revision.content_revision
    assert document.text == text and document.metadata == {}
    assert document.structure.total_length == len(text)
    assert document.structure.anchors == parsed.anchors
    for actual, source in zip(
        document.structure.sections, parsed.sections, strict=True
    ):
        assert actual == replace(
            source,
            section_id=rag_section_id(
                resource_id="resource",
                content_revision=revision.content_revision,
                common_section_id=source.section_id,
            ),
        )


@pytest.mark.parametrize(
    "override",
    [
        {"resourceId": ""},
        {"resourceId": None},
        {"resourceId": 123},
        {"version": None},
        {"version": True},
        {"version": "7"},
        {"version": 7.0},
        {"content": None},
        {"content": 123},
    ],
)
def test_content_event_rejects_invalid_external_fields(override):
    with pytest.raises(ValidationError):
        DocumentReadyEvent.model_validate(
            {"resourceId": "resource", "version": 7, "content": "正文", **override}
        )


def test_destroy_event_flattens_all_resource_types_without_inventing_type_policy():
    event = ResourceDestroyedEvent.model_validate(
        {"typedResourceIds": {"DOCUMENT": ["a", "b"], "UNKNOWN": ["b", "c"]}}
    )
    assert event.resource_ids == ["a", "b", "b", "c"]
    assert (
        ResourceDestroyedEvent.model_validate({"typedResourceIds": {}}).resource_ids
        == []
    )


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"typedResourceIds": None},
        {"typedResourceIds": []},
        {"typedResourceIds": {"DOCUMENT": "resource"}},
        {"typedResourceIds": {"DOCUMENT": [""]}},
        {"typedResourceIds": {"DOCUMENT": [None]}},
        {"typedResourceIds": {"DOCUMENT": [123]}},
    ],
)
def test_destroy_event_rejects_invalid_external_groups(payload):
    with pytest.raises(ValidationError):
        ResourceDestroyedEvent.model_validate(payload)


async def test_publication_started_requires_both_main_consumers(consumer, kafka):
    client, factory = kafka
    allowed, destroy_entered = asyncio.Event(), asyncio.Event()

    async def delayed_start():
        destroy_entered.set()
        await allowed.wait()

    async def idle():
        await asyncio.Event().wait()

    factory.destroy_client.start.side_effect = delayed_start
    client.getone.side_effect = idle
    ready = asyncio.Event()
    task = asyncio.create_task(consumer.run(started=ready))
    try:
        await asyncio.wait_for(destroy_entered.wait(), 1)
        assert not ready.is_set()
        allowed.set()
        await asyncio.wait_for(ready.wait(), 1)
        assert not task.done()
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    client.stop.assert_awaited_once()
    factory.destroy_client.stop.assert_awaited_once()


async def test_failed_publication_start_never_signals_ready_and_stops_both(
    consumer, kafka
):
    client, factory = kafka
    factory.destroy_client.start.side_effect = OSError("broker unavailable")

    async def idle():
        await asyncio.Event().wait()

    client.getone.side_effect = idle
    ready = asyncio.Event()
    with pytest.raises(OSError):
        await asyncio.wait_for(consumer.run(started=ready), 1)
    assert not ready.is_set()
    client.stop.assert_awaited_once()
    factory.destroy_client.stop.assert_awaited_once()
