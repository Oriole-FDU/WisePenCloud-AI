from dataclasses import dataclass

import pytest

from rag.api.kafka import (
    AclRecalculateHandler,
    DocumentReadyHandler,
    KafkaEventConsumer,
    KafkaPayloadError,
    ResourceDestroyHandler,
)
from rag.application.rag.acl import AuthoritativeAclNotFoundError


class _Indexer:
    def __init__(self) -> None:
        self.calls = []

    async def index_resource(self, **kwargs):
        self.calls.append(kwargs)


class _Refresher:
    def __init__(self, *, missing=False) -> None:
        self.calls = []
        self.missing = missing

    async def refresh(self, resource_id):
        self.calls.append(resource_id)
        if self.missing:
            raise AuthoritativeAclNotFoundError(resource_id)


class _Deleter:
    def __init__(self) -> None:
        self.calls = []

    async def delete_resources(self, resource_ids):
        self.calls.append(resource_ids)


@pytest.mark.asyncio
async def test_event_handlers_validate_and_call_only_owned_use_case() -> None:
    indexer = _Indexer()
    refresher = _Refresher()
    deleter = _Deleter()

    await DocumentReadyHandler(indexer=indexer).handle(
        {"resourceId": "resource-1", "version": 3, "content": "# Title"}
    )
    await AclRecalculateHandler(refresher=refresher).handle(
        {"resourceId": "resource-1"}
    )
    await ResourceDestroyHandler(deleter=deleter).handle(
        {
            "typedResourceIds": {
                "document": ["resource-1", "resource-2"],
                "note": ["resource-1"],
            }
        }
    )

    assert indexer.calls == [
        {
            "resource_id": "resource-1",
            "document_version": 3,
            "markdown": "# Title",
        }
    ]
    assert refresher.calls == ["resource-1"]
    assert deleter.calls == [["resource-1", "resource-2"]]


@pytest.mark.asyncio
async def test_invalid_payload_is_classified_without_calling_application() -> None:
    indexer = _Indexer()

    with pytest.raises(KafkaPayloadError):
        await DocumentReadyHandler(indexer=indexer).handle(
            {"resourceId": "resource-1", "version": "3", "content": "text"}
        )

    assert indexer.calls == []


@pytest.mark.asyncio
async def test_missing_authoritative_acl_is_a_terminal_noop() -> None:
    refresher = _Refresher(missing=True)

    await AclRecalculateHandler(refresher=refresher).handle(
        {"resourceId": "deleted-resource"}
    )

    assert refresher.calls == ["deleted-resource"]


@dataclass
class _Message:
    value: object
    partition: int = 0
    offset: int = 7


class _Consumer:
    def __init__(self, messages=None, *, start_error=None) -> None:
        self.messages = list(messages or [])
        self.start_error = start_error
        self.started = False
        self.stopped = False
        self.commits = 0

    async def start(self):
        if self.start_error is not None:
            raise self.start_error
        self.started = True

    async def stop(self):
        self.stopped = True

    async def commit(self):
        self.commits += 1

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self.messages:
            raise StopAsyncIteration
        return self.messages.pop(0)


class _Producer:
    def __init__(self, *, send_failures=0, **kwargs) -> None:
        self.started = False
        self.stopped = False
        self.messages = []
        self.send_failures = send_failures

    async def start(self):
        self.started = True

    async def stop(self):
        self.stopped = True

    async def send_and_wait(self, topic, *, value):
        if self.send_failures:
            self.send_failures -= 1
            raise RuntimeError("dead-letter broker unavailable")
        self.messages.append((topic, value))


def _event_consumer(fake, handler, **kwargs):
    return KafkaEventConsumer(
        bootstrap_servers="kafka:9092",
        topic="topic",
        group_id="group",
        handler=handler,
        retry_delay_seconds=0,
        consumer_factory=lambda *args, **kwargs: fake,
        **kwargs,
    )


@pytest.mark.asyncio
async def test_handler_failure_retries_same_offset_before_single_commit() -> None:
    fake = _Consumer([_Message({"resourceId": "resource-1"})])
    attempts = 0

    async def handler(payload):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("temporary failure")

    consumer = _event_consumer(fake, handler)
    consumer._consumer = fake

    await consumer._consume_loop()

    assert attempts == 2
    assert fake.commits == 1


@pytest.mark.asyncio
async def test_invalid_message_commits_once_without_calling_handler() -> None:
    fake = _Consumer([_Message(b"not-json")])
    called = False

    async def handler(payload):
        nonlocal called
        called = True

    consumer = _event_consumer(fake, handler)
    consumer._consumer = fake

    await consumer._consume_loop()

    assert called is False
    assert fake.commits == 1


@pytest.mark.asyncio
async def test_permanent_handler_failure_is_moved_to_dead_letter_after_limit() -> None:
    fake = _Consumer([_Message({"resourceId": "resource-1"})])
    producer = _Producer()
    attempts = 0

    async def handler(payload):
        nonlocal attempts
        attempts += 1
        raise RuntimeError("index backend unavailable")

    consumer = _event_consumer(
        fake,
        handler,
        max_delivery_attempts=2,
        dead_letter_topic="failed-events",
        producer_factory=lambda *args, **kwargs: producer,
    )
    consumer._consumer = fake
    consumer._dead_letter_producer = producer

    await consumer._consume_loop()

    assert attempts == 2
    assert fake.commits == 1
    assert producer.messages == [
        (
            "failed-events",
            {
                "source_topic": "topic",
                "source_partition": 0,
                "source_offset": 7,
                "delivery_attempts": 2,
                "error_type": "RuntimeError",
                "error": "index backend unavailable",
                "payload": {"resourceId": "resource-1"},
            },
        )
    ]


@pytest.mark.asyncio
async def test_invalid_message_is_preserved_in_dead_letter() -> None:
    fake = _Consumer([_Message(b"not-json")])
    producer = _Producer()
    consumer = _event_consumer(
        fake,
        lambda payload: None,
        dead_letter_topic="failed-events",
    )
    consumer._consumer = fake
    consumer._dead_letter_producer = producer

    await consumer._consume_loop()

    assert fake.commits == 1
    assert producer.messages[0][1]["error_type"] == "KafkaPayloadError"
    assert producer.messages[0][1]["payload"] == {
        "encoding": "base64",
        "data": "bm90LWpzb24=",
    }


@pytest.mark.asyncio
async def test_dead_letter_publish_retries_before_committing_source_offset() -> None:
    fake = _Consumer([_Message({"resourceId": "resource-1"})])
    producer = _Producer(send_failures=1)

    async def handler(payload):
        raise RuntimeError("permanent failure")

    consumer = _event_consumer(
        fake,
        handler,
        max_delivery_attempts=1,
        dead_letter_topic="failed-events",
        producer_factory=lambda *args, **kwargs: producer,
    )
    consumer._consumer = fake
    consumer._dead_letter_producer = producer

    await consumer._consume_loop()

    assert fake.commits == 1
    assert len(producer.messages) == 1


@pytest.mark.asyncio
async def test_consumer_start_failure_propagates_and_stop_closes_started_client() -> None:
    failure = _Consumer(start_error=RuntimeError("unavailable"))
    consumer = _event_consumer(failure, lambda payload: None)

    with pytest.raises(RuntimeError, match="unavailable"):
        await consumer.start()

    fake = _Consumer()
    consumer = _event_consumer(fake, lambda payload: None)
    await consumer.start()
    await consumer.stop()

    assert fake.started is True
    assert fake.stopped is True


@pytest.mark.asyncio
async def test_consumer_lifecycle_starts_and_stops_dead_letter_producer() -> None:
    fake = _Consumer()
    producer = _Producer()

    async def handler(payload):
        return None

    consumer = _event_consumer(
        fake,
        handler,
        dead_letter_topic="failed-events",
        producer_factory=lambda *args, **kwargs: producer,
    )

    await consumer.start()
    await consumer.stop()

    assert producer.started is True
    assert producer.stopped is True
