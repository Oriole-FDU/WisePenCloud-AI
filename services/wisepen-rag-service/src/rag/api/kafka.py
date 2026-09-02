"""Kafka transport：校验上游事件，并把失败消息隔离到死信 topic。"""

from __future__ import annotations

import asyncio
import base64
import json
from collections.abc import Awaitable, Callable, Mapping
from typing import Annotated, Any

from aiokafka import AIOKafkaConsumer, AIOKafkaProducer
from common.logger import error, info, warn
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError

NonEmptyText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
EventHandler = Callable[[Mapping[str, Any]], Awaitable[None]]


class KafkaPayloadError(ValueError):
    """事件正文不符合上游契约，不应重复消费。"""


class DocumentReadyPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    resource_id: NonEmptyText = Field(alias="resourceId")
    version: int = Field(ge=1)
    content: str


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


def _validate_payload(
    model_type: type[BaseModel], payload: Mapping[str, Any]
) -> BaseModel:
    try:
        return model_type.model_validate(payload)
    except ValidationError as exc:
        raise KafkaPayloadError(str(exc)) from exc


def validated_handler(
    model_type: type[BaseModel],
    handler: EventHandler,
) -> EventHandler:
    """把 Kafka 外部字段转换为 application 使用的已校验事实。"""

    async def handle(payload: Mapping[str, Any]) -> None:
        message = _validate_payload(model_type, payload)
        if isinstance(message, DocumentReadyPayload):
            await handler(
                {
                    "resource_id": message.resource_id,
                    "version": message.version,
                    "content": message.content,
                }
            )
        elif isinstance(message, AclRecalculatePayload):
            await handler({"resource_id": message.resource_id})
        else:
            await handler({"resource_ids": message.resource_ids})

    return handle


class KafkaEventConsumer:
    """手动提交 offset，失败重试，无法处理的消息进入死信 topic。"""

    def __init__(
        self,
        *,
        bootstrap_servers: str,
        topic: str,
        group_id: str,
        handler: EventHandler,
        retry_delay_seconds: float,
        max_delivery_attempts: int,
        dead_letter_topic: str | None,
    ) -> None:
        if not bootstrap_servers.strip() or not topic.strip() or not group_id.strip():
            raise ValueError("Kafka bootstrap servers, topic and group ID are required")
        self._bootstrap_servers = bootstrap_servers
        self._topic = topic
        self._group_id = group_id
        self._handler = handler
        self._retry_delay_seconds = retry_delay_seconds
        self._max_delivery_attempts = max_delivery_attempts
        self._dead_letter_topic = (
            dead_letter_topic.strip() if dead_letter_topic else None
        )
        self._consumer = None
        self._producer = None
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self._task is not None:
            return
        self._consumer = AIOKafkaConsumer(
            self._topic,
            bootstrap_servers=self._bootstrap_servers,
            group_id=self._group_id,
            enable_auto_commit=False,
        )
        await self._consumer.start()
        if self._dead_letter_topic:
            self._producer = AIOKafkaProducer(
                bootstrap_servers=self._bootstrap_servers,
                value_serializer=lambda value: json.dumps(
                    value, ensure_ascii=False
                ).encode(),
            )
            try:
                await self._producer.start()
            except Exception:
                await self._consumer.stop()
                self._consumer = None
                raise
        self._task = asyncio.create_task(
            self._consume_loop(), name=f"rag-v3-kafka-{self._topic}"
        )
        info(
            "rag v3 kafka consumer started.", topic=self._topic, group_id=self._group_id
        )

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
        if self._producer is not None:
            await self._producer.stop()
            self._producer = None

    async def _consume_loop(self) -> None:
        if self._consumer is None:
            raise RuntimeError("Kafka consumer is not started")
        async for message in self._consumer:
            try:
                payload = self._decode_message(message.value)
                for attempt in range(1, self._max_delivery_attempts + 1):
                    try:
                        await self._handler(payload)
                        await self._consumer.commit()
                        break
                    except Exception as exc:  # noqa: BLE001
                        if attempt == self._max_delivery_attempts:
                            await self._quarantine(message, exc, attempt)
                            break
                        error(
                            "rag v3 kafka event will retry.",
                            topic=self._topic,
                            attempt=attempt,
                            exc=exc,
                        )
                        await asyncio.sleep(self._retry_delay_seconds)
            except KafkaPayloadError as exc:
                await self._quarantine(message, exc, 1)

    async def _quarantine(
        self, message: Any, exception: BaseException, attempts: int
    ) -> None:
        if self._producer is None or self._dead_letter_topic is None:
            await self._consumer.commit()
            return
        record = {
            "source_topic": self._topic,
            "source_partition": message.partition,
            "source_offset": message.offset,
            "delivery_attempts": attempts,
            "error_type": type(exception).__name__,
            "error": str(exception),
            "payload": _dead_letter_payload(message.value),
        }
        while True:
            try:
                await self._producer.send_and_wait(
                    self._dead_letter_topic, value=record
                )
                break
            except Exception as exc:  # noqa: BLE001
                error(
                    "rag v3 dead-letter publish will retry.", topic=self._topic, exc=exc
                )
                await asyncio.sleep(self._retry_delay_seconds)
        await self._consumer.commit()
        warn(
            "rag v3 kafka event moved to dead-letter topic.",
            topic=self._topic,
            exc=exception,
        )

    @staticmethod
    def _decode_message(value: object) -> dict[str, Any]:
        try:
            if isinstance(value, Mapping):
                decoded = dict(value)
            elif isinstance(value, (bytes, bytearray, memoryview)):
                decoded = json.loads(bytes(value).decode("utf-8"))
            else:
                decoded = json.loads(str(value))
            if isinstance(decoded, str):
                decoded = json.loads(decoded)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise KafkaPayloadError("Kafka payload is not valid JSON") from exc
        if not isinstance(decoded, dict):
            raise KafkaPayloadError("Kafka payload is not a JSON object")
        return decoded


def _dead_letter_payload(value: object) -> object:
    if isinstance(value, (bytes, bytearray, memoryview)):
        return {
            "encoding": "base64",
            "data": base64.b64encode(bytes(value)).decode("ascii"),
        }
    if value is None or isinstance(value, (str, int, float, bool, list, dict)):
        return value
    return repr(value)
