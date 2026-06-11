from aiokafka import AIOKafkaProducer
import json
from common.logger import error, info
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
            info("kafka producer started.", bootstrap_servers=self.bootstrap_servers)
        except Exception as e:
            error("kafka producer start failed.", exc=e)

    async def stop(self):
        if self._producer:
            await self._producer.stop()
            info("kafka producer stopped.")

    async def send(self, topic: str, value: Dict, headers: List[Tuple[str, bytes]] = None):
        if not self._producer:
            error("kafka publish failed because producer is not started", topic=topic)
            return
        try:
            await self._producer.send_and_wait(topic, value=value, headers=headers)
        except Exception as e:
            error("kafka publish failed.", topic=topic, exc=e)
       
