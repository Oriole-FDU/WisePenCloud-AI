"""校验事实事件并以不越过失败 offset 的方式驱动 application 用例。"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable, Mapping
from typing import Annotated, Any

from aiokafka import AIOKafkaConsumer
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
    """永久非法正文提交后跳过，真实处理失败保留当前 offset 并原地重试。"""

    def __init__(
        self,
        *,
        bootstrap_servers: str,
        topic: str,
        group_id: str,
        handler: EventHandler,
        retry_delay_seconds: float = 1.0,
        consumer_factory: Callable[..., Any] = AIOKafkaConsumer,
    ) -> None:
        if not bootstrap_servers.strip() or not topic.strip() or not group_id.strip():
            raise ValueError("Kafka bootstrap servers, topic and group ID are required")
        if retry_delay_seconds < 0:
            raise ValueError("retry_delay_seconds must not be negative")
        self._bootstrap_servers = bootstrap_servers
        self._topic = topic
        self._group_id = group_id
        self._handler = handler
        self._retry_delay_seconds = retry_delay_seconds
        self._consumer_factory = consumer_factory
        self._consumer = None
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

    async def _consume_loop(self) -> None:
        if self._consumer is None:
            raise RuntimeError("Kafka consumer is not started")
        async for message in self._consumer:
            payload = None
            while True:
                try:
                    payload = payload or self._decode_message(message.value)
                    await self._handler(payload)
                    await self._consumer.commit()
                    break
                except KafkaPayloadError as exception:
                    warn(
                        "rag kafka payload rejected.",
                        topic=self._topic,
                        partition=message.partition,
                        offset=message.offset,
                        exc=exception,
                    )
                    await self._consumer.commit()
                    break
                # application 与外部依赖可能抛出任意普通异常；都必须保留 offset 重试。
                except Exception as exception:  # noqa: BLE001
                    error(
                        "rag kafka event will retry.",
                        topic=self._topic,
                        partition=message.partition,
                        offset=message.offset,
                        exc=exception,
                    )
                    await asyncio.sleep(self._retry_delay_seconds)

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
