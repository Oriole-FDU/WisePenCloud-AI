"""
前端工具响应的 Kafka 消费
"""
from __future__ import annotations

import uuid

from chat.application.frontend_tool_response_broker import FrontendToolResponseBroker
from chat.core.constants import KAFKA_CHAT_TURN_RESPONSE_TOPIC
from common.kafka.consumer import KafkaConsumerClient


class FrontendToolResponseConsumer:
    def __init__(
        self,
        *,
        bootstrap_servers: str,
        service_name: str,
        broker: FrontendToolResponseBroker,
    ) -> None:
        self._client = KafkaConsumerClient(
            bootstrap_servers=bootstrap_servers,
            topic=KAFKA_CHAT_TURN_RESPONSE_TOPIC,
            group_id=f"{service_name}-frontend-responses-{uuid.uuid4().hex}",
            handler=broker.handle_message,
        )

    async def start(self) -> None:
        if not await self._client.start():
            raise RuntimeError("frontend tool response Kafka consumer failed to start")

    async def stop(self) -> None:
        await self._client.stop()
