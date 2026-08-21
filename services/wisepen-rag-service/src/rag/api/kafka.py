"""校验事实事件并以不越过失败 offset 的方式驱动 application 用例。"""

from __future__ import annotations

import asyncio
import base64
import json
from collections.abc import Awaitable, Callable, Mapping
from typing import Annotated, Any

from aiokafka import AIOKafkaConsumer, AIOKafkaProducer
from common.logger import error, info, warn
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError

from rag.application.rag.acl import (
    AuthoritativeAclNotFoundError,
    ResourceAclRefresher,
)
from rag.application.rag.index import ResourceDeleter, ResourceIndexer

NonEmptyText = Annotated[
    str,
    StringConstraints(strict=True, strip_whitespace=True, min_length=1),
]
EventHandler = Callable[[Mapping[str, Any]], Awaitable[None]]


class KafkaPayloadError(ValueError):
    """事件正文不符合不可变的上游契约，不应重复消费。"""


class DocumentReadyPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    resource_id: NonEmptyText = Field(alias="resourceId")
    version: Annotated[int, Field(strict=True, ge=1)]
    content: Annotated[str, Field(strict=True)]


class AclRecalculatePayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    resource_id: NonEmptyText = Field(alias="resourceId")


class ResourceDestroyPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    typed_resource_ids: dict[NonEmptyText, list[NonEmptyText]] = Field(
        alias="typedResourceIds"
    )

    @property
    def resource_ids(self) -> list[str]:
        return list(
            dict.fromkeys(
                resource_id
                for resource_ids in self.typed_resource_ids.values()
                for resource_id in resource_ids
            )
        )


class DocumentReadyHandler:
    def __init__(self, *, indexer: ResourceIndexer) -> None:
        self._indexer = indexer

    async def handle(self, payload: Mapping[str, Any]) -> None:
        message = _validate_payload(DocumentReadyPayload, payload)
        await self._indexer.index_resource(
            resource_id=message.resource_id,
            document_version=message.version,
            markdown=message.content,
        )


class AclRecalculateHandler:
    def __init__(self, *, refresher: ResourceAclRefresher) -> None:
        self._refresher = refresher

    async def handle(self, payload: Mapping[str, Any]) -> None:
        message = _validate_payload(AclRecalculatePayload, payload)
        try:
            await self._refresher.refresh(message.resource_id)
        except AuthoritativeAclNotFoundError:
            # ACL 重算事件可以晚于资源删除到达；资源已不存在时无需永久重试。
            warn(
                "rag acl refresh skipped for missing authoritative resource.",
                resource_id=message.resource_id,
            )


class ResourceDestroyHandler:
    def __init__(self, *, deleter: ResourceDeleter) -> None:
        self._deleter = deleter

    async def handle(self, payload: Mapping[str, Any]) -> None:
        resource_ids = _validate_payload(ResourceDestroyPayload, payload).resource_ids
        if resource_ids:
            await self._deleter.delete_resources(resource_ids)


def _validate_payload(model_type, payload: Mapping[str, Any]):
    try:
        return model_type.model_validate(payload)
    except ValidationError as e:
        raise KafkaPayloadError(str(e)) from e


class KafkaEventConsumer:
    """消费 RAG 事件，并把无法处理的消息隔离到死信 topic。"""

    def __init__(
        self,
        *,
        bootstrap_servers: str,
        topic: str,
        group_id: str,
        handler: EventHandler,
        retry_delay_seconds: float = 1.0,
        max_delivery_attempts: int = 3,
        dead_letter_topic: str | None = None,
        consumer_factory: Callable[..., Any] = AIOKafkaConsumer,
        producer_factory: Callable[..., Any] = AIOKafkaProducer,
    ) -> None:
        if not bootstrap_servers.strip() or not topic.strip() or not group_id.strip():
            raise ValueError("Kafka bootstrap servers, topic and group ID are required")
        if retry_delay_seconds < 0:
            raise ValueError("retry_delay_seconds must not be negative")
        if max_delivery_attempts < 1:
            raise ValueError("max_delivery_attempts must be at least 1")
        self._bootstrap_servers = bootstrap_servers
        self._topic = topic
        self._group_id = group_id
        self._handler = handler
        self._retry_delay_seconds = retry_delay_seconds
        self._max_delivery_attempts = max_delivery_attempts
        self._dead_letter_topic = (
            dead_letter_topic.strip() or None
            if dead_letter_topic is not None
            else None
        )
        self._consumer_factory = consumer_factory
        self._producer_factory = producer_factory
        self._consumer = None
        self._dead_letter_producer = None
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self._task is not None:
            return
        consumer = self._consumer_factory(
            self._topic,
            bootstrap_servers=self._bootstrap_servers,
            group_id=self._group_id,
            enable_auto_commit=False,
        )
        await consumer.start()
        self._consumer = consumer
        if self._dead_letter_topic:
            producer = self._producer_factory(
                bootstrap_servers=self._bootstrap_servers,
                value_serializer=lambda value: json.dumps(
                    value,
                    ensure_ascii=False,
                ).encode("utf-8"),
            )
            try:
                await producer.start()
            except Exception:
                await consumer.stop()
                self._consumer = None
                raise
            self._dead_letter_producer = producer
        self._task = asyncio.create_task(
            self._consume_loop(),
            name=f"rag-v2-kafka-{self._topic}",
        )
        info("rag kafka consumer started.", topic=self._topic, group_id=self._group_id)

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        if self._consumer is not None:
            await self._consumer.stop()
            self._consumer = None
            info(
                "rag kafka consumer stopped.",
                topic=self._topic,
                group_id=self._group_id,
            )
        if self._dead_letter_producer is not None:
            await self._dead_letter_producer.stop()
            self._dead_letter_producer = None

    async def _consume_loop(self) -> None:
        if self._consumer is None:
            raise RuntimeError("Kafka consumer is not started")
        async for message in self._consumer:
            try:
                payload = self._decode_message(message.value)
            except KafkaPayloadError as exception:
                await self._quarantine_message(
                    message,
                    exception=exception,
                    attempts=1,
                )
                continue

            for attempt in range(1, self._max_delivery_attempts + 1):
                try:
                    await self._handler(payload)
                    await self._consumer.commit()
                    break
                # application 与外部依赖可能抛出任意普通异常；都必须保留 offset 重试。
                except Exception as exception:  # noqa: BLE001
                    if attempt == self._max_delivery_attempts:
                        await self._quarantine_message(
                            message,
                            exception=exception,
                            attempts=attempt,
                        )
                        break
                    error(
                        "rag kafka event will retry.",
                        topic=self._topic,
                        partition=message.partition,
                        offset=message.offset,
                        attempt=attempt,
                        max_attempts=self._max_delivery_attempts,
                        exc=exception,
                    )
                    await asyncio.sleep(self._retry_delay_seconds)

    async def _quarantine_message(
        self,
        message: Any,
        *,
        exception: BaseException,
        attempts: int,
    ) -> None:
        """将毒性消息可靠转交死信 topic，再提交原消息 offset。"""
        if self._dead_letter_producer is None or self._dead_letter_topic is None:
            # 未配置隔离出口时必须提交 offset，否则一个永久失败会阻塞整个 partition。
            error(
                "rag kafka event dropped after delivery attempts because dead-letter "
                "topic is not configured.",
                topic=self._topic,
                partition=message.partition,
                offset=message.offset,
                attempts=attempts,
                exc=exception,
            )
            await self._consumer.commit()
            return

        record = {
            "source_topic": self._topic,
            "source_partition": message.partition,
            "source_offset": message.offset,
            "delivery_attempts": attempts,
            "error_type": type(exception).__name__,
            "error": str(exception),
            # 保留原始 bytes，便于死信消费者重放或人工审计。
            "payload": _dead_letter_payload(message.value),
        }
        while True:
            try:
                await self._dead_letter_producer.send_and_wait(
                    self._dead_letter_topic,
                    value=record,
                )
                break
            except Exception as send_exception:  # noqa: BLE001
                # 原消息只有在死信 broker 确认后才能提交；否则恢复后仍需重试隔离。
                error(
                    "rag kafka dead-letter publish will retry.",
                    topic=self._topic,
                    dead_letter_topic=self._dead_letter_topic,
                    partition=message.partition,
                    offset=message.offset,
                    exc=send_exception,
                )
                await asyncio.sleep(self._retry_delay_seconds)
        await self._consumer.commit()
        warn(
            "rag kafka event moved to dead-letter topic.",
            topic=self._topic,
            dead_letter_topic=self._dead_letter_topic,
            partition=message.partition,
            offset=message.offset,
            attempts=attempts,
            exc=exception,
        )

    @staticmethod
    def _decode_message(value: object) -> dict[str, Any]:
        try:
            if isinstance(value, Mapping):
                return dict(value)
            if isinstance(value, (bytes, bytearray, memoryview)):
                decoded = json.loads(bytes(value).decode("utf-8"))
            else:
                decoded = json.loads(str(value))
            if isinstance(decoded, str):
                decoded = json.loads(decoded)
        except (UnicodeDecodeError, json.JSONDecodeError) as exception:
            raise KafkaPayloadError("Kafka payload is not valid JSON") from exception
        if not isinstance(decoded, dict):
            raise KafkaPayloadError("Kafka payload is not a JSON object")
        return decoded


def _dead_letter_payload(value: object) -> object:
    """把 Kafka 原始值转换为死信 envelope 可 JSON 编码的字段。"""
    if isinstance(value, (bytes, bytearray, memoryview)):
        return {
            "encoding": "base64",
            "data": base64.b64encode(bytes(value)).decode("ascii"),
        }
    if value is None or isinstance(value, (str, int, float, bool, list, dict)):
        return value
    return repr(value)
