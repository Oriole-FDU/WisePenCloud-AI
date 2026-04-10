import asyncio
import json
from typing import Dict, List, Tuple, Optional
from aiokafka import AIOKafkaProducer

from common.logger import log_error, log_event, log_ok



class KafkaProducerClient:
    def __init__(self, bootstrap_servers: str):
        self._producer: Optional[AIOKafkaProducer] = None
        self._bootstrap_servers = bootstrap_servers

    async def start(self):
        try:
            self._producer = AIOKafkaProducer(
                bootstrap_servers=self._bootstrap_servers,
                value_serializer=lambda x: json.dumps(x, ensure_ascii=False).encode('utf-8'),
                acks="all",
                enable_idempotence=True,
                retries=3,
            )
            await self._producer.start() # type: ignore
            log_ok(f"Kafka Producer 启动", bootstrap_servers=self._bootstrap_servers)
        except Exception as e:
            log_error("Kafka Producer 启动", e)


    async def stop(self):
        if self._producer:
            await self._producer.stop()
            log_event("Kafka Producer 已停止")

    async def send(self, topic: str, value: Dict, key: Optional[str] = None,headers: Optional[List[Tuple[str, bytes]]] = None) -> bool:
        if not self._producer:
            log_error("Kafka 发送", "Producer 未启动或启动失败")
            return False

        try:
            await self._producer.send_and_wait(topic,
                                               key=key.encode('utf-8') if key else None,
                                               value=value,
                                               headers=headers)
            return True
        except Exception as e:
            log_error("Kafka 发送", e, topic=topic)
            return False

