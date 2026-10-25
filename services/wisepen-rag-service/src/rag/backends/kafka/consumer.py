"""JSON 事件的进程内快速重试与 DLQ；转发确认后才提交当前分区 offset。"""

import asyncio
import json
import math
import traceback
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import StrEnum

from aiokafka import AIOKafkaConsumer, AIOKafkaProducer, ConsumerRecord, TopicPartition


@dataclass(frozen=True, slots=True, kw_only=True)
class RetryPolicy:
    backoff_seconds: list[float] = field(default_factory=lambda: [1, 2, 4])


class DeliveryHeader(StrEnum):
    """保留 DLQ 的原消息位置和错误诊断；不保存重试状态。"""

    ORIGINAL_TOPIC = "rag-original-topic"
    ORIGINAL_PARTITION = "rag-original-partition"
    ORIGINAL_OFFSET = "rag-original-offset"
    EXCEPTION_TYPE = "rag-exception-type"
    EXCEPTION_MESSAGE = "rag-exception-message"
    EXCEPTION_TRACEBACK = "rag-exception-traceback"


_DELIVERY_HEADERS = set(DeliveryHeader)


class KafkaConsumerClient:
    """管理消息确认；ValueError 永久失败，其他业务异常只在当前进程有限重试。"""

    def __init__(
            self,
            *,
            bootstrap_servers: str,
            topic: str,
            group_id: str,
            handler: Callable[[dict[str, object]], Awaitable[object]],
            max_poll_interval_ms: int = 300_000,
            retry_policy: RetryPolicy | None = None,
            delivery_timeout_ms: int = 30_000,
    ) -> None:
        self._bootstrap_servers = bootstrap_servers
        self._topic = topic
        self._group_id = group_id
        self._handler = handler
        if delivery_timeout_ms <= 0:
            raise ValueError("delivery_timeout_ms must be positive")
        self._delivery_timeout_ms = delivery_timeout_ms
        self._retry_policy = retry_policy if retry_policy is not None else RetryPolicy()
        self._dlq_topic = f"{topic}.DLT"
        # 部署边界拒绝不可结束的等待；重试次数不写入跨消息 headers。
        for delay in self._retry_policy.backoff_seconds:
            if not math.isfinite(delay) or delay < 0:
                raise ValueError("backoff delays must be finite and non-negative")
        self._max_poll_interval_ms = max_poll_interval_ms

    async def run(self, *, started: asyncio.Event | None = None) -> None:
        """前台运行至失败或取消；默认重试等待 1/2/4 秒，然后交给人工处理 DLQ。

        发送与输入确认之间崩溃允许重复投递，应用仍须幂等。基础设施失败
        向外传播，不伪装成业务成功；运行入口观察循环退出并关闭共享资源。
        """
        producer = AIOKafkaProducer(
            bootstrap_servers=self._bootstrap_servers,
            acks="all",
            request_timeout_ms=self._delivery_timeout_ms,
        )
        try:
            await producer.start()
            # 只有一个主消费者，不为已删除的延迟层保留后台任务编排。
            await self._consume(producer, started=started)
        finally:
            await producer.stop()

    async def _consume(
            self, producer: AIOKafkaProducer, *, started: asyncio.Event | None = None
    ) -> None:
        consumer = AIOKafkaConsumer(
            self._topic,
            bootstrap_servers=self._bootstrap_servers,
            group_id=self._group_id,
            enable_auto_commit=False,
            auto_offset_reset="earliest",
            max_poll_interval_ms=self._max_poll_interval_ms,
        )
        try:
            await consumer.start()
            if started is not None:
                started.set()
            while True:
                message = await consumer.getone()
                await self._handle_main(message, producer)
                # offset 是下一条记录；只确认当前分区，DLQ 发送失败不会走到这里。
                await consumer.commit(
                    {
                        TopicPartition(message.topic, message.partition): message.offset
                                                                          + 1
                    }
                )
        finally:
            await consumer.stop()

    async def _handle_main(
            self, message: ConsumerRecord, producer: AIOKafkaProducer
    ) -> None:
        try:
            payload = _decode_message(message.value)
        except (TypeError, ValueError) as error:
            await self._forward(message, producer, error)
            return

        delays = self._retry_policy.backoff_seconds
        for attempt in range(len(delays) + 1):
            try:
                await self._handler(payload)
                return
            except Exception as error:  # noqa: BLE001 - 有限隔离后可靠交付 DLQ，供人工重放。
                if isinstance(error, ValueError) or attempt == len(delays):
                    await self._forward(message, producer, error)
                    return
            # 只重试业务调用，发送、确认和连接错误不进入此预算。
            await asyncio.sleep(delays[attempt])

    async def _forward(
            self, message: ConsumerRecord, producer: AIOKafkaProducer, error: Exception
    ) -> None:
        headers = [
            (key, value)
            for key, value in message.headers
            if key not in _DELIVERY_HEADERS
        ]
        headers.extend(
            [
                (DeliveryHeader.ORIGINAL_TOPIC, message.topic.encode()),
                (DeliveryHeader.ORIGINAL_PARTITION, str(message.partition).encode()),
                (DeliveryHeader.ORIGINAL_OFFSET, str(message.offset).encode()),
                (
                    DeliveryHeader.EXCEPTION_TYPE,
                    f"{type(error).__module__}.{type(error).__name__}".encode(),
                ),
                # 错误文本和 traceback 有界，不让异常对象无限扩大消息 headers。
                (DeliveryHeader.EXCEPTION_MESSAGE, str(error)[:4096].encode("utf-8")),
                (
                    DeliveryHeader.EXCEPTION_TRACEBACK,
                    "".join(traceback.format_exception(error))[:8192].encode("utf-8"),
                ),
            ]
        )
        # 超时可能已经送达；未确认输入允许重复投递，由应用幂等承接。
        await asyncio.wait_for(
            producer.send_and_wait(
                self._dlq_topic, value=message.value, key=message.key, headers=headers
            ),
            timeout=self._delivery_timeout_ms / 1000,
        )


def _decode_message(value: bytes | None) -> dict[str, object]:
    if value is None:
        raise ValueError("Kafka event cannot be a tombstone")
    payload = json.loads(value.decode("utf-8"))
    if isinstance(payload, str):
        # 上游允许 JSON 字符串包裹对象，只解开协议规定的一层。
        payload = json.loads(payload)
    if not isinstance(payload, dict):
        raise TypeError("Kafka message payload must be a JSON object")
    return payload
