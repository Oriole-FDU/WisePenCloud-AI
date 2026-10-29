"""用队列驱动真实传输编排，验证快速重试、DLQ 和基础设施失败边界。"""

import asyncio
import time
from collections import defaultdict
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from aiokafka import TopicPartition

from rag.core.kafka import (
    DeliveryHeader,
    KafkaConsumerClient,
    RetryPolicy,
)


class Broker:
    """仅替代 aiokafka I/O；消费者与快速重试均执行生产代码。"""

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
    monkeypatch.setattr("rag.core.messaging.kafka.AIOKafkaConsumer", broker.factory)
    monkeypatch.setattr(
        "rag.core.messaging.kafka.AIOKafkaProducer", broker.producer_factory
    )
    return broker


def client(policy=None):
    return KafkaConsumerClient(
        bootstrap_servers="localhost:9092",
        topic="main",
        group_id="group",
        retry_policy=policy,
    )


async def run_until(consumer, broker, handler, predicate):
    task = asyncio.create_task(consumer.run(handler))
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
    await run_until(client(), broker, handler, lambda: len(broker.commits) == 1)
    handler.assert_awaited_once_with({"n": 1})
    assert broker.commits == [{TopicPartition("main", 2): 11}]
    assert broker.sent == []
    broker.producer_factory.assert_called_once_with(
        bootstrap_servers="localhost:9092", acks="all", request_timeout_ms=30_000
    )
    assert set(broker.clients) == {"main"}
    kwargs = broker.factory.call_args_list[-1].kwargs
    assert kwargs["max_poll_interval_ms"] == 300_000
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

    monkeypatch.setattr("rag.core.messaging.kafka.asyncio.sleep", sleep)
    handler = AsyncMock(side_effect=OSError("downstream failed"))
    broker.put(broker.record())
    await run_until(
        client(RetryPolicy()), broker, handler, lambda: len(broker.commits) == 1
    )
    assert waits == [1, 2, 4] and handler.await_count == 4
    assert broker.sent[0].topic == "main.DLT"
    assert all(type(key) is str for key, _ in broker.sent[0].headers)
    headers = dict(broker.sent[0].headers)
    assert headers[DeliveryHeader.ORIGINAL_OFFSET.value] == b"10"
    assert headers[DeliveryHeader.EXCEPTION_TYPE.value] == b"builtins.OSError"
    assert b"Traceback" in headers[DeliveryHeader.EXCEPTION_TRACEBACK.value]


async def test_unknown_program_error_has_only_process_local_retry_budget(broker):
    handler = AsyncMock(side_effect=KeyError("unexpected contract"))
    broker.put(broker.record(headers=[("upstream", b"original")]))
    await run_until(
        client(RetryPolicy(backoff_seconds=[0, 0, 0])),
        broker,
        handler,
        lambda: len(broker.commits) == 1,
    )
    assert handler.await_count == 4
    assert set(broker.clients) == {"main"}
    assert dict(broker.sent[0].headers)["upstream"] == b"original"
    assert (
            dict(broker.sent[0].headers)[DeliveryHeader.EXCEPTION_TYPE.value]
            == b"builtins.KeyError"
    )


async def test_quick_retry_can_recover_without_forwarding(broker):
    handler = AsyncMock(side_effect=[OSError("temporary"), None])
    broker.put(broker.record())
    await run_until(
        client(RetryPolicy(backoff_seconds=[0])),
        broker,
        handler,
        lambda: len(broker.commits) == 1,
    )
    assert handler.await_count == 2 and broker.sent == []


@pytest.mark.parametrize("value", [None, b"{", b"\xff", b"null", b"[]", b'"x"'])
async def test_bad_message_is_forwarded_once_before_offset_commit(broker, value):
    handler = AsyncMock()
    message = broker.record(value=value, key=None)
    broker.put(message)
    await run_until(client(), broker, handler, lambda: len(broker.commits) == 1)
    handler.assert_not_awaited()
    assert len(broker.sent) == 1 and broker.sent[0].topic == "main.DLT"
    assert broker.sent[0].value == value and broker.sent[0].key is None


async def test_permanent_handler_failure_goes_directly_to_dlq(broker):
    handler = AsyncMock(side_effect=ValueError("permanent conflict"))
    broker.put(broker.record())
    await run_until(
        client(),
        broker,
        handler,
        lambda: len(broker.commits) == 1,
    )
    assert handler.await_count == 1


async def test_forward_failure_propagates_without_committing_source(broker):
    error = RuntimeError("broker publish failed")
    broker.producer.send_and_wait.side_effect = error
    handler = AsyncMock(side_effect=OSError("downstream failure"))
    policy = RetryPolicy(backoff_seconds=[])
    broker.put(broker.record())
    with pytest.raises(RuntimeError) as caught:
        await client(policy).run(handler)
    assert caught.value is error and broker.commits == []
    for consumer in broker.clients.values():
        consumer.stop.assert_awaited_once()
    broker.producer.stop.assert_awaited_once()


async def test_commit_failure_after_successful_forward_preserves_published_dlq(
        broker,
):
    error = RuntimeError("commit failed")
    broker.commit_error = error
    policy = RetryPolicy(backoff_seconds=[])
    broker.put(broker.record())
    handler = AsyncMock(side_effect=OSError("downstream"))
    with pytest.raises(RuntimeError) as caught:
        await client(policy).run(handler)
    assert caught.value is error and broker.commits == []
    assert len(broker.sent) == 1 and broker.sent[0].topic == "main.DLT"


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
        await client().run(AsyncMock())
    assert caught.value is error and broker.commits == []
    broker.producer.stop.assert_awaited_once()
    for consumer in broker.clients.values():
        consumer.stop.assert_awaited_once()


@pytest.mark.parametrize("delay", [-1, float("nan"), float("inf")])
def test_invalid_wait_budget_is_rejected_before_kafka_io(delay):
    with pytest.raises(ValueError):
        client(RetryPolicy(backoff_seconds=[delay]))


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
    task = asyncio.create_task(client().run(AsyncMock(), started=started))
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
        delivery_timeout_ms=10,
    )
    with pytest.raises(TimeoutError):
        await consumer.run(AsyncMock())
    assert broker.commits == []
    broker.producer.stop.assert_awaited_once()
    for connection in broker.clients.values():
        connection.stop.assert_awaited_once()
