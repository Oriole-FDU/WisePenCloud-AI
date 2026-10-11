"""显式测试 broker 上验证真实消费、延迟回投、DLQ 与 offset 确认。"""

import asyncio
import json
import os
from uuid import uuid4

import pytest
import pytest_asyncio
from aiokafka import AIOKafkaConsumer, AIOKafkaProducer, TopicPartition
from aiokafka.admin import AIOKafkaAdminClient, NewTopic

from rag.application.publication import PublicationEventConsumer
from rag.backends.kafka import DeliveryHeader, RetryPolicy, RetryTopic

pytestmark = pytest.mark.kafka_integration


@pytest_asyncio.fixture
async def kafka_broker():
    servers = os.environ.get("RAG_TEST_KAFKA_BOOTSTRAP_SERVERS")
    if not servers:
        pytest.skip("未配置 RAG_TEST_KAFKA_BOOTSTRAP_SERVERS，跳过真实 Kafka 测试")
    identity = uuid4().hex
    topics = [f"codex-rag-content-{identity}", f"codex-rag-destroy-{identity}"]
    policies = [
        RetryPolicy(
            backoff_seconds=[0, 0, 0],
            delayed_retries=[
                RetryTopic(f"{topic}-retry-a", 0.05),
                RetryTopic(f"{topic}-retry-b", 0.05),
            ],
        )
        for topic in topics
    ]
    all_topics = [
        *topics,
        *[stage.topic for policy in policies for stage in policy.delayed_retries],
        *[f"{topic}.DLT" for topic in topics],
    ]
    group_id = f"codex-rag-publication-{identity}"
    admin = AIOKafkaAdminClient(bootstrap_servers=servers, request_timeout_ms=5000)
    producer = AIOKafkaProducer(bootstrap_servers=servers, request_timeout_ms=5000)
    observer = AIOKafkaConsumer(
        bootstrap_servers=servers,
        group_id=group_id,
        enable_auto_commit=False,
        request_timeout_ms=5000,
    )
    created = False
    try:
        await admin.start()
        response = await admin.create_topics(
            [
                NewTopic(topic, num_partitions=1, replication_factor=1)
                for topic in all_topics
            ]
        )
        created = True
        assert all(error[1] == 0 for error in response.topic_errors), (
            response.topic_errors
        )
        await producer.start()
        await observer.start()
        yield servers, topics, group_id, producer, observer, policies
    finally:
        await observer.stop()
        await producer.stop()
        try:
            if created:
                # 仅删除本 fixture 的随机主、重试与 DLQ topic，不触及业务 topic。
                await admin.delete_topics(all_topics)
        finally:
            await admin.close()


async def wait_for_commit(observer, partition, offset):
    while await observer.committed(partition) != offset:
        await asyncio.sleep(0.1)


async def test_real_kafka_transient_failure_retries_then_publishes_both_versions(
    kafka_broker, publication, publication_store, projection
):
    servers, topics, group_id, producer, observer, policies = kafka_broker
    first = await producer.send_and_wait(
        topics[0],
        key=b"resource",
        value=json.dumps(
            {"resourceId": "resource", "version": 1, "content": "正文"}
        ).encode(),
    )
    later = await producer.send_and_wait(
        topics[0],
        key=b"resource",
        value=json.dumps(
            {"resourceId": "resource", "version": 2, "content": "新版"}
        ).encode(),
    )
    consumer = PublicationEventConsumer(
        publisher=publication,
        bootstrap_servers=servers,
        group_id=group_id,
        document_ready_topic=topics[0],
        physical_destroy_topic=topics[1],
        content_retry_policy=policies[0],
        destroy_retry_policy=policies[1],
    )
    error = RuntimeError("required projection failed")
    projection.index.side_effect = [error, None, None]
    partition = TopicPartition(first.topic, first.partition)
    task = asyncio.create_task(consumer.run())
    try:
        await asyncio.wait_for(
            wait_for_commit(observer, partition, later.offset + 1), timeout=45
        )
        state = (await publication_store.get_states(["resource"]))[0]
        assert (
            state.applied_revision.document_version == 2
            and state.staged_revision is None
        )
        assert projection.index.await_count == 3
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


async def test_real_kafka_destroy_before_content_is_permanent_and_both_are_acknowledged(
    kafka_broker, publication, publication_store, projection
):
    servers, topics, group_id, producer, observer, policies = kafka_broker
    consumer = PublicationEventConsumer(
        publisher=publication,
        bootstrap_servers=servers,
        group_id=group_id,
        document_ready_topic=topics[0],
        physical_destroy_topic=topics[1],
        content_retry_policy=policies[0],
        destroy_retry_policy=policies[1],
    )
    destroy = await producer.send_and_wait(
        topics[1],
        key=b"resource",
        value=json.dumps({"typedResourceIds": {"DOCUMENT": ["resource"]}}).encode(),
    )
    task = asyncio.create_task(consumer.run())
    try:
        await asyncio.wait_for(
            wait_for_commit(
                observer,
                TopicPartition(destroy.topic, destroy.partition),
                destroy.offset + 1,
            ),
            timeout=45,
        )
        ready = await producer.send_and_wait(
            topics[0],
            key=b"resource",
            value=json.dumps(
                {"resourceId": "resource", "version": 1, "content": "迟到正文"}
            ).encode(),
        )
        await asyncio.wait_for(
            wait_for_commit(
                observer, TopicPartition(ready.topic, ready.partition), ready.offset + 1
            ),
            timeout=45,
        )
        state = (await publication_store.get_states(["resource"]))[0]
        assert state.destroyed and state.applied_revision is None
        projection.index.assert_not_awaited()
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


async def test_real_kafka_retry_topics_return_to_main_then_dlq_keeps_original_record(
    kafka_broker, publication, publication_store, projection
):
    servers, topics, group_id, producer, observer, policies = kafka_broker
    payload = json.dumps(
        {"resourceId": "resource", "version": 1, "content": "正文😀"}
    ).encode()
    original = await producer.send_and_wait(topics[0], key=b"resource", value=payload)
    consumer = PublicationEventConsumer(
        publisher=publication,
        bootstrap_servers=servers,
        group_id=group_id,
        document_ready_topic=topics[0],
        physical_destroy_topic=topics[1],
        content_retry_policy=policies[0],
        destroy_retry_policy=policies[1],
    )
    dlq = AIOKafkaConsumer(
        f"{topics[0]}.DLT",
        bootstrap_servers=servers,
        group_id=f"{group_id}-dlq",
        enable_auto_commit=False,
        auto_offset_reset="earliest",
    )
    projection.index.side_effect = OSError("required projection unavailable")
    task = asyncio.create_task(consumer.run())
    try:
        await dlq.start()
        terminal = await asyncio.wait_for(dlq.getone(), timeout=45)
        assert terminal.value == payload and terminal.key == b"resource"
        headers = dict(terminal.headers)
        assert headers[DeliveryHeader.ORIGINAL_TOPIC] == original.topic.encode()
        assert (
            headers[DeliveryHeader.ORIGINAL_PARTITION]
            == str(original.partition).encode()
        )
        assert headers[DeliveryHeader.ORIGINAL_OFFSET] == str(original.offset).encode()
        assert headers[DeliveryHeader.RETRY_COUNT] == b"5"
        assert headers[DeliveryHeader.EXCEPTION_TYPE] == b"builtins.OSError"
        assert projection.index.await_count == 6
        state = (await publication_store.get_states(["resource"]))[0]
        assert state.applied_revision is None and state.staged_revision is not None
        # DLQ 发送与源确认分开，等候主分区确认最后一次回投记录。
        partition = TopicPartition(original.topic, original.partition)
        await asyncio.wait_for(
            wait_for_commit(observer, partition, original.offset + 3), timeout=45
        )
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await dlq.stop()
