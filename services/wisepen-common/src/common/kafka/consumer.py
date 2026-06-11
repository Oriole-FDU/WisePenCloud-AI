from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from typing import Any, Optional

from aiokafka import AIOKafkaConsumer

from common.logger import error, info


MessageHandler = Callable[[dict[str, Any]], Awaitable[None]]


class KafkaConsumerClient:
    def __init__(
        self,
        *,
        bootstrap_servers: str,
        topic: str,
        group_id: str,
        handler: MessageHandler,
    ) -> None:
        self.bootstrap_servers = bootstrap_servers
        self.topic = topic
        self.group_id = group_id
        self._handler = handler
        self._consumer: Optional[AIOKafkaConsumer] = None
        self._task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        if self._task is not None:
            return
        self._consumer = AIOKafkaConsumer(
            self.topic,
            bootstrap_servers=self.bootstrap_servers,
            group_id=self.group_id,
            enable_auto_commit=False,
        )
        try:
            await self._consumer.start()
        except Exception as e:
            error("kafka consumer start failed.", topic=self.topic, group_id=self.group_id, exc=e)
            self._consumer = None
            return
        self._task = asyncio.create_task(self._consume_loop(), name=f"kafka-consumer-{self.topic}")
        info("kafka consumer started.", topic=self.topic, group_id=self.group_id)

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
            info("kafka consumer stopped.", topic=self.topic, group_id=self.group_id)

    async def _consume_loop(self) -> None:
        assert self._consumer is not None
        try:
            async for msg in self._consumer:
                try:
                    payload = self._decode_message(msg.value)
                    await self._handler(payload)
                    await self._consumer.commit()
                except Exception as e:
                    error(
                        "kafka event consumption failed.",
                        exc=e,
                        topic=self.topic,
                        partition=msg.partition,
                        offset=msg.offset,
                    )
        except asyncio.CancelledError:
            raise
        except Exception as e:
            error("kafka consumer loop failed.", topic=self.topic, group_id=self.group_id, exc=e)

    @staticmethod
    def _decode_message(value: bytes | bytearray | memoryview | str | dict[str, Any]) -> dict[str, Any]:
        if isinstance(value, dict):
            return value
        if isinstance(value, (bytes, bytearray, memoryview)):
            text = bytes(value).decode("utf-8")
        else:
            text = str(value)
        decoded = json.loads(text)
        if isinstance(decoded, str):
            decoded = json.loads(decoded)
        if not isinstance(decoded, dict):
            raise ValueError("Kafka message payload is not a JSON object")
        return decoded
