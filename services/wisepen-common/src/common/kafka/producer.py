from aiokafka import AIOKafkaProducer
import json
from common.logger import log_error, log_event
from typing import Dict, List, Tuple, Optional


class KafkaProducerClient:
    def __init__(self, bootstrap_servers: str):
        self._producer: Optional[AIOKafkaProducer] = None
        self.bootstrap_servers = bootstrap_servers

    async def start(self):
        try:
            self._producer = AIOKafkaProducer(
                bootstrap_servers=self.bootstrap_servers,
                value_serializer=lambda x: json.dumps(x, ensure_ascii=False).encode('utf-8'),
            )
            await self._producer.start()
            log_event(f"Kafka Producer 已启动", bootstrap_servers=self.bootstrap_servers)
        except Exception as e:
            log_error("Kafka Producer 启动", e)

    async def stop(self):
        if self._producer:
            await self._producer.stop()
            log_event("Kafka Producer 已停止")

    async def send(self, topic: str, value: Dict, headers: List[Tuple[str, bytes]] = None):
        if not self._producer:
            log_error("Kafka 发送", "Producer 未启动或启动失败")
            return
        try:
            await self._producer.send_and_wait(topic, value=value, headers=headers)
        except Exception as e:
            log_error("Kafka 发送", e, topic=topic)
       
