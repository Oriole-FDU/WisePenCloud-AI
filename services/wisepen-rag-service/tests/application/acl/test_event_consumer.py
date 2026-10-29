"""ACL 已迁移到 RAG Kafka 后端，投影成功、重试或 DLQ 后才确认输入。"""

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from aiokafka import TopicPartition

from rag.application.acl import AclEventConsumer, AuthoritativeAclError
from rag.core.kafka import KafkaConsumerClient, RetryPolicy


@pytest.mark.parametrize("failure", [None, "temporary", "permanent"])
async def test_acl_uses_shared_backend_and_correct_confirmation_policy(
        monkeypatch, failure
):
    message = SimpleNamespace(
        topic="acl",
        partition=1,
        offset=9,
        key=b"resource",
        value=b'{"resourceId":"resource"}',
        headers=[],
        timestamp=int(time.time() * 1000),
    )
    driver = SimpleNamespace(
        start=AsyncMock(),
        stop=AsyncMock(),
        getone=AsyncMock(side_effect=[message, asyncio.CancelledError()]),
        commit=AsyncMock(),
    )
    producer = SimpleNamespace(
        start=AsyncMock(), stop=AsyncMock(), send_and_wait=AsyncMock()
    )
    monkeypatch.setattr(
        "rag.core.messaging.kafka.AIOKafkaConsumer", Mock(return_value=driver)
    )
    monkeypatch.setattr(
        "rag.core.messaging.kafka.AIOKafkaProducer", Mock(return_value=producer)
    )
    projection = SimpleNamespace(handle_event=AsyncMock())
    if failure == "temporary":
        projection.handle_event.side_effect = [
            OSError("Mongo unavailable"),
            ["resource"],
        ]
    elif failure == "permanent":
        projection.handle_event.side_effect = AuthoritativeAclError(
            "invalid authority fields"
        )
    else:
        projection.handle_event.return_value = ["resource"]

    async def commit(offsets):
        if failure == "permanent":
            producer.send_and_wait.assert_awaited_once()
        else:
            assert projection.handle_event.await_count == (
                2 if failure == "temporary" else 1
            )
        assert offsets == {TopicPartition("acl", 1): 10}

    driver.commit.side_effect = commit
    consumer = AclEventConsumer(
        projection=projection,
        consumer=KafkaConsumerClient(
            bootstrap_servers="localhost:9092",
            topic="acl",
            group_id="rag-acl",
            retry_policy=RetryPolicy(backoff_seconds=[0]),
        ),
    )
    with pytest.raises(asyncio.CancelledError):
        await consumer.run()
    if failure == "permanent":
        assert producer.send_and_wait.call_args.args == ("acl.DLT",)
    else:
        producer.send_and_wait.assert_not_awaited()
    driver.stop.assert_awaited_once()
    producer.stop.assert_awaited_once()
