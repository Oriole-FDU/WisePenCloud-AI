"""用队列驱动真实传输编排，验证重试链、回投时序、DLQ 和基础设施失败边界。"""

import asyncio
import json
import time
from collections import defaultdict
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from aiokafka import TopicPartition

from rag.application.publication import (
    DocumentPublication,
    DocumentReadyEvent,
    PublicationEventConsumer,
)
from rag.backends.kafka import (
    DeliveryHeader,
    KafkaConsumerClient,
    RetryPolicy,
    RetryTopic,
)
from rag.ingestion.chunkers import MarkdownChunker
from rag.ingestion.indexers import ChunkFactsIndexer
from rag.ingestion.pipeline import IngestionPipeline


class Broker:
    """仅替代 aiokafka I/O；消费者、重试和回投均执行生产代码。"""

    def __init__(self):
        self.queues = defaultdict(asyncio.Queue)
        self.sent = []
        self.commits = []
        self.clients = {}
        self.changed = asyncio.Event()
        self.commit_error = None
        self.factory = Mock(side_effect=self.client)
        self.producer = SimpleNamespace(
            start=AsyncMock(),
            stop=AsyncMock(),
            send_and_wait=AsyncMock(side_effect=self.send),
        )
        self.producer_factory = Mock(return_value=self.producer)

    def record(
        self,
        *,
        topic="main",
        value=b'{"n":1}',
        key=b"resource",
        partition=2,
        offset=10,
        headers=None,
    ):
        return SimpleNamespace(
            topic=topic,
            value=value,
            key=key,
            partition=partition,
            offset=offset,
            headers=headers if headers is not None else [],
            timestamp=int(time.time() * 1000),
        )

    def put(self, message):
        self.queues[message.topic].put_nowait(message)

    def client(self, topic, **kwargs):
        async def commit(offsets):
            if self.commit_error is not None:
                raise self.commit_error
            self.commits.append(offsets)
            self.changed.set()

        client = SimpleNamespace(
            start=AsyncMock(),
            stop=AsyncMock(),
            getone=AsyncMock(side_effect=self.queues[topic].get),
            commit=AsyncMock(side_effect=commit),
        )
        self.clients[topic] = client
        return client

    async def send(self, topic, *, value, key, headers, partition=0):
        message = self.record(
            topic=topic,
            value=value,
            key=key,
            headers=headers,
            partition=partition if partition is not None else 0,
            offset=100 + len(self.sent),
        )
        self.sent.append(message)
        self.put(message)
        return SimpleNamespace(topic=topic, partition=partition, offset=message.offset)

    async def until(self, predicate):
        while not predicate():
            self.changed.clear()
            if predicate():
                break
            await self.changed.wait()


@pytest.fixture
def broker(monkeypatch):
    broker = Broker()
    monkeypatch.setattr("rag.backends.kafka.consumer.AIOKafkaConsumer", broker.factory)
    monkeypatch.setattr(
        "rag.backends.kafka.consumer.AIOKafkaProducer", broker.producer_factory
    )
    return broker


def client(handler, policy=None, is_retryable=None):
    return KafkaConsumerClient(
        bootstrap_servers="localhost:9092",
        topic="main",
        group_id="group",
        handler=handler,
        retry_policy=policy,
        is_retryable=is_retryable,
    )


async def run_until(consumer, broker, predicate):
    task = asyncio.create_task(consumer.run())
    try:
        await asyncio.wait_for(broker.until(predicate), timeout=2)
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


@pytest.mark.parametrize("value", [b'{"n":1}', b'"{\\"n\\":1}"'])
async def test_success_decodes_wire_format_then_commits_exact_partition(broker, value):
    handler = AsyncMock()
    broker.put(broker.record(value=value))
    await run_until(client(handler), broker, lambda: len(broker.commits) == 1)
    handler.assert_awaited_once_with({"n": 1})
    assert broker.commits == [{TopicPartition("main", 2): 11}]
    assert broker.sent == []
    broker.producer_factory.assert_called_once_with(
        bootstrap_servers="localhost:9092", acks="all", request_timeout_ms=30_000
    )
    assert set(broker.clients) == {"main", "main-retry-1m", "main-retry-10m"}
    kwargs = broker.factory.call_args_list[-1].kwargs
    assert kwargs["max_poll_interval_ms"] >= 630_000
    for consumer in broker.clients.values():
        consumer.stop.assert_awaited_once()
    broker.producer.stop.assert_awaited_once()


async def test_quick_retry_backoff_is_one_two_four_seconds_then_dlq(
    broker, monkeypatch
):
    waits = []
    original_sleep = asyncio.sleep

    async def sleep(delay):
        waits.append(delay)
        await original_sleep(0)

    monkeypatch.setattr("rag.backends.kafka.consumer.asyncio.sleep", sleep)
    handler = AsyncMock(side_effect=OSError("downstream failed"))
    broker.put(broker.record())
    await run_until(
        client(handler, RetryPolicy()), broker, lambda: len(broker.commits) == 1
    )
    assert waits == [1, 2, 4] and handler.await_count == 4
    assert broker.sent[0].topic == "main.DLT"
    assert dict(broker.sent[0].headers)[DeliveryHeader.RETRY_COUNT] == b"3"


async def test_five_retries_run_only_on_main_and_preserve_origin_key_and_bytes(broker):
    stages = [RetryTopic("retry-1", 0.01), RetryTopic("retry-2", 0.01)]
    policy = RetryPolicy(backoff_seconds=[0, 0, 0], delayed_retries=stages)
    handler = AsyncMock(side_effect=OSError("中文错误"))
    message = broker.record(headers=[("upstream", b"original")])
    broker.put(message)
    await run_until(client(handler, policy), broker, lambda: len(broker.commits) == 5)
    assert handler.await_count == 6
    assert [item.topic for item in broker.sent] == [
        "retry-1",
        "main",
        "retry-2",
        "main",
        "main.DLT",
    ]
    for sent in broker.sent:
        assert sent.value == message.value and sent.key == message.key
        headers = dict(sent.headers)
        assert headers[DeliveryHeader.ORIGINAL_TOPIC] == b"main"
        assert headers[DeliveryHeader.ORIGINAL_PARTITION] == b"2"
        assert headers[DeliveryHeader.ORIGINAL_OFFSET] == b"10"
        assert headers["upstream"] == b"original"
        if sent.topic == "main":
            assert sent.partition == 2
    terminal = dict(broker.sent[-1].headers)
    assert terminal[DeliveryHeader.RETRY_COUNT] == b"5"
    assert terminal[DeliveryHeader.EXCEPTION_TYPE] == b"builtins.OSError"
    assert terminal[DeliveryHeader.EXCEPTION_MESSAGE].decode() == "中文错误"
    assert b"OSError" in terminal[DeliveryHeader.EXCEPTION_TRACEBACK]
    assert DeliveryHeader.AVAILABLE_AT not in terminal


async def test_delayed_failure_does_not_block_later_main_message(broker):
    calls = []
    policy = RetryPolicy(
        backoff_seconds=[], delayed_retries=[RetryTopic("retry", 0.05)]
    )

    async def handle(payload):
        calls.append(payload["n"])
        if payload["n"] == 1:
            raise OSError("temporary failure")

    broker.put(broker.record())
    broker.put(broker.record(value=b'{"n":2}', offset=11))
    await run_until(client(handle, policy), broker, lambda: len(broker.commits) == 4)
    assert calls == [1, 2, 1]
    assert broker.commits[1] == {TopicPartition("main", 2): 12}


async def test_quick_retry_can_recover_without_forwarding(broker):
    handler = AsyncMock(side_effect=[OSError("temporary"), None])
    broker.put(broker.record())
    await run_until(
        client(handler, RetryPolicy(backoff_seconds=[0])),
        broker,
        lambda: len(broker.commits) == 1,
    )
    assert handler.await_count == 2 and broker.sent == []


@pytest.mark.parametrize("value", [None, b"{", b"\xff", b"null", b"[]", b'"x"'])
async def test_bad_message_is_forwarded_once_before_offset_commit(broker, value):
    handler = AsyncMock()
    message = broker.record(value=value, key=None)
    broker.put(message)
    await run_until(client(handler), broker, lambda: len(broker.commits) == 1)
    handler.assert_not_awaited()
    assert len(broker.sent) == 1 and broker.sent[0].topic == "main.DLT"
    assert broker.sent[0].value == value and broker.sent[0].key is None
    assert dict(broker.sent[0].headers)[DeliveryHeader.RETRY_COUNT] == b"0"


async def test_permanent_handler_failure_goes_directly_to_dlq(broker):
    handler = AsyncMock(side_effect=ValueError("permanent conflict"))
    broker.put(broker.record())
    await run_until(
        client(handler, is_retryable=lambda error: not isinstance(error, ValueError)),
        broker,
        lambda: len(broker.commits) == 1,
    )
    assert handler.await_count == 1
    assert dict(broker.sent[0].headers)[DeliveryHeader.RETRY_COUNT] == b"0"


def retry_headers(*, count=4, available_at=None):
    headers = [
        (DeliveryHeader.ORIGINAL_TOPIC, b"main"),
        (DeliveryHeader.ORIGINAL_PARTITION, b"2"),
        (DeliveryHeader.ORIGINAL_OFFSET, b"10"),
        (DeliveryHeader.RETRY_COUNT, str(count).encode()),
    ]
    if available_at is not None:
        headers.append((DeliveryHeader.AVAILABLE_AT, str(available_at).encode()))
    return headers


@pytest.mark.parametrize(
    "change", ["count", "topic", "partition", "missing", "null", "partial"]
)
async def test_invalid_delivery_headers_are_dead_lettered_without_handler(
    broker, change
):
    headers = dict(retry_headers())
    if change == "count":
        headers[DeliveryHeader.RETRY_COUNT] = b"99"
    elif change == "topic":
        headers[DeliveryHeader.ORIGINAL_TOPIC] = b"foreign-topic"
    elif change == "partition":
        headers[DeliveryHeader.ORIGINAL_PARTITION] = b"-1"
    elif change == "null":
        headers[DeliveryHeader.ORIGINAL_TOPIC] = None
    elif change == "partial":
        del headers[DeliveryHeader.RETRY_COUNT]
    else:
        del headers[DeliveryHeader.ORIGINAL_OFFSET]
    handler = AsyncMock()
    broker.put(broker.record(headers=list(headers.items())))
    await run_until(client(handler), broker, lambda: len(broker.commits) == 1)
    handler.assert_not_awaited()
    assert broker.sent[0].topic == "main.DLT"


@pytest.mark.parametrize(
    "invalid", ["missing-deadline", "wrong-count", "unbounded-deadline"]
)
async def test_invalid_delayed_message_goes_to_dlq_without_business_handler(
    broker, invalid
):
    policy = RetryPolicy(
        backoff_seconds=[0, 0, 0], delayed_retries=[RetryTopic("retry", 0.01)]
    )
    available = (
        None
        if invalid == "missing-deadline"
        else int(time.time() * 1000)
        + (100_000 if invalid == "unbounded-deadline" else 1)
    )
    count = 3 if invalid == "wrong-count" else 4
    handler = AsyncMock()
    broker.put(
        broker.record(
            topic="retry", headers=retry_headers(count=count, available_at=available)
        )
    )
    await run_until(client(handler, policy), broker, lambda: len(broker.commits) == 1)
    handler.assert_not_awaited()
    assert broker.sent[0].topic == "main.DLT"


@pytest.mark.parametrize("target", ["retry", "dlq"])
async def test_forward_failure_propagates_without_committing_source(broker, target):
    error = RuntimeError("broker publish failed")
    broker.producer.send_and_wait.side_effect = error
    handler = AsyncMock(side_effect=OSError("downstream failure"))
    policy = RetryPolicy(
        backoff_seconds=[],
        delayed_retries=[RetryTopic("retry", 1)] if target == "retry" else [],
    )
    broker.put(broker.record())
    with pytest.raises(RuntimeError) as caught:
        await client(handler, policy).run()
    assert caught.value is error and broker.commits == []
    for consumer in broker.clients.values():
        consumer.stop.assert_awaited_once()
    broker.producer.stop.assert_awaited_once()


async def test_commit_failure_after_successful_forward_preserves_published_retry(
    broker,
):
    error = RuntimeError("commit failed")
    broker.commit_error = error
    policy = RetryPolicy(backoff_seconds=[], delayed_retries=[RetryTopic("retry", 60)])
    broker.put(broker.record())
    with pytest.raises(RuntimeError) as caught:
        await client(AsyncMock(side_effect=OSError("downstream")), policy).run()
    assert caught.value is error and broker.commits == []
    assert len(broker.sent) == 1 and broker.sent[0].topic == "retry"


@pytest.mark.parametrize("stage", ["producer-start", "consumer-start", "getone"])
async def test_infrastructure_failure_closes_all_resources_and_propagates(
    broker, stage
):
    error = RuntimeError(stage)
    if stage == "producer-start":
        broker.producer.start.side_effect = error
    else:
        original_factory = broker.factory.side_effect

        def create(topic, **kwargs):
            result = original_factory(topic, **kwargs)
            if topic == "main":
                getattr(
                    result, "start" if stage == "consumer-start" else "getone"
                ).side_effect = error
            return result

        broker.factory.side_effect = create
    with pytest.raises(RuntimeError) as caught:
        await client(AsyncMock()).run()
    assert caught.value is error and broker.commits == []
    broker.producer.stop.assert_awaited_once()
    for consumer in broker.clients.values():
        consumer.stop.assert_awaited_once()


@pytest.mark.parametrize("delay", [-1, float("nan"), float("inf")])
def test_invalid_wait_budget_is_rejected_before_kafka_io(delay):
    with pytest.raises(ValueError):
        client(AsyncMock(), RetryPolicy(backoff_seconds=[delay]))
    with pytest.raises(ValueError):
        client(AsyncMock(), RetryPolicy(delayed_retries=[RetryTopic("retry", delay)]))


def test_topic_collisions_are_rejected_before_kafka_io():
    with pytest.raises(ValueError):
        client(AsyncMock(), RetryPolicy(delayed_retries=[RetryTopic("main", 1)]))


async def test_startup_signal_waits_for_producer_and_main_connection(broker):
    entering, allow, started = [asyncio.Event() for _ in range(3)]
    factory = broker.factory.side_effect

    async def connect():
        entering.set()
        await allow.wait()

    def create(topic, **kwargs):
        result = factory(topic, **kwargs)
        if topic == "main":
            result.start.side_effect = connect
        return result

    broker.factory.side_effect = create
    task = asyncio.create_task(client(AsyncMock()).run(started=started))
    try:
        await asyncio.wait_for(entering.wait(), timeout=2)
        assert not started.is_set()
        allow.set()
        await asyncio.wait_for(started.wait(), timeout=2)
        broker.producer.start.assert_awaited_once()
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


async def test_send_deadline_stops_without_ack_and_closes_resources(broker):
    async def unavailable(*args, **kwargs):
        await asyncio.Event().wait()

    broker.producer.send_and_wait.side_effect = unavailable
    broker.put(broker.record(value=b"bad JSON"))
    consumer = KafkaConsumerClient(
        bootstrap_servers="localhost:9092",
        topic="main",
        group_id="group",
        handler=AsyncMock(),
        delivery_timeout_ms=10,
    )
    with pytest.raises(TimeoutError):
        await consumer.run()
    assert broker.commits == []
    broker.producer.stop.assert_awaited_once()
    for connection in broker.clients.values():
        connection.stop.assert_awaited_once()


async def test_delayed_old_revision_is_skipped_after_newer_main_revision_applies(
    broker, storage, publication_store
):
    projection = SimpleNamespace(
        index=AsyncMock(side_effect=[OSError("temporary failure"), None])
    )
    publisher = DocumentPublication(
        store=publication_store,
        reader=storage,
        writer=storage,
        pipeline=IngestionPipeline(
            chunker=MarkdownChunker(),
            transformers=[],
            indexers=[ChunkFactsIndexer(writer=storage), projection],
        ),
    )
    consumer = PublicationEventConsumer(
        publisher=publisher,
        bootstrap_servers="localhost:9092",
        group_id="group",
        document_ready_topic="main",
        physical_destroy_topic="destroy",
        content_retry_policy=RetryPolicy(
            backoff_seconds=[], delayed_retries=[RetryTopic("retry", 0.05)]
        ),
        destroy_retry_policy=RetryPolicy(backoff_seconds=[]),
    )
    for version in [1, 2]:
        payload = {"resourceId": "resource", "version": version, "content": "正文"}
        broker.put(
            broker.record(value=json.dumps(payload).encode(), offset=9 + version)
        )
    await run_until(consumer, broker, lambda: len(broker.commits) == 4)
    assert projection.index.await_count == 2
    state = (await publication_store.get_states(["resource"]))[0]
    assert (
        state.applied_revision.document_version == 2 and state.staged_revision is None
    )
    assert [item.topic for item in broker.sent] == ["retry", "main"]
    old_revision = (
        DocumentReadyEvent.model_validate(
            {"resourceId": "resource", "version": 1, "content": "正文"}
        )
        .to_document()
        .content_revision
    )
    assert await storage.get_revision_chunks(
        resource_id="resource", content_revision=old_revision
    )
