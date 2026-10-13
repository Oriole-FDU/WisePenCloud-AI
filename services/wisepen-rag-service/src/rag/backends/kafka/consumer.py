"""JSON 事件的快速重试、延迟回投与 DLQ；业务处理始终在主 topic 串行执行。"""

import asyncio
import json
import math
import time
import traceback
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from aiokafka import AIOKafkaConsumer, AIOKafkaProducer, ConsumerRecord, TopicPartition

from .retry import DeliveryHeader, RetryPolicy, RetryTopic

_DELIVERY_HEADERS = set(DeliveryHeader)
_RELAY_SLACK_SECONDS = 30


@dataclass(frozen=True, slots=True)
class _Delivery:
    topic: str
    partition: int
    offset: int
    retry_count: int = 0
    available_at_ms: int | None = None


class KafkaConsumerClient:
    """管理重试和确认；应用提供 handler、幂等合同及不可重试异常的分类。"""

    def __init__(
        self,
        *,
        bootstrap_servers: str,
        topic: str,
        group_id: str,
        handler: Callable[[dict[str, object]], Awaitable[object]],
        max_poll_interval_ms: int = 300_000,
        retry_policy: RetryPolicy | None = None,
        is_retryable: Callable[[Exception], bool] | None = None,
        delivery_timeout_ms: int = 30_000,
    ) -> None:
        self._bootstrap_servers = bootstrap_servers
        self._topic = topic
        self._group_id = group_id
        self._handler = handler
        if delivery_timeout_ms <= 0:
            raise ValueError("delivery_timeout_ms must be positive")
        self._delivery_timeout_ms = delivery_timeout_ms
        self._is_retryable = is_retryable or (lambda error: True)
        self._retry_policy = retry_policy or RetryPolicy(
            delayed_retries=[
                RetryTopic(f"{topic}-retry-1m", 60),
                RetryTopic(f"{topic}-retry-10m", 600),
            ]
        )
        self._dlq_topic = f"{topic}.DLT"
        # 这是部署配置边界：负数或非有限等待会破坏重试预算和消费者存活合同。
        for delay in self._retry_policy.backoff_seconds:
            if not math.isfinite(delay) or delay < 0:
                raise ValueError("backoff delays must be finite and non-negative")
        for stage in self._retry_policy.delayed_retries:
            if not math.isfinite(stage.delay_seconds) or stage.delay_seconds < 0:
                raise ValueError("retry topic delays must be finite and non-negative")
        topics = [
            topic,
            self._dlq_topic,
            *[stage.topic for stage in self._retry_policy.delayed_retries],
        ]
        if len(set(topics)) != len(topics):
            raise ValueError("main, retry and DLQ topics must be distinct")
        # 必须覆盖完整单条业务处理耗时；实际预算由运行入口配置。
        self._max_poll_interval_ms = max_poll_interval_ms

    async def run(self, *, started: asyncio.Event | None = None) -> None:
        """前台运行直到基础设施失败或取消；入口必须等待并负责重新启动。

        每条链默认最多重试五次。快速重试发生在主分区，延迟队列只等候并回投；
        新版本可先于旧重试到达，旧版本淘汰由应用负责。转发先等待 broker 确认，
        再提交输入 offset；两者之间崩溃允许重复投递，应用必须保持幂等。
        """
        producer = AIOKafkaProducer(
            bootstrap_servers=self._bootstrap_servers,
            acks="all",
            request_timeout_ms=self._delivery_timeout_ms,
        )
        tasks = []
        try:
            await producer.start()
            tasks.append(
                asyncio.create_task(
                    self._consume(self._topic, producer, started=started)
                )
            )
            for stage in self._retry_policy.delayed_retries:
                tasks.append(
                    asyncio.create_task(self._consume(stage.topic, producer, stage))
                )
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await producer.stop()

    async def _consume(
        self,
        topic: str,
        producer: AIOKafkaProducer,
        stage: RetryTopic | None = None,
        *,
        started: asyncio.Event | None = None,
    ) -> None:
        # 延迟 relay 的等待不占主循环；它的 poll 预算须覆盖整个延迟窗口。
        poll_interval = self._max_poll_interval_ms
        if stage is not None:
            poll_interval = max(
                poll_interval,
                math.ceil((stage.delay_seconds + _RELAY_SLACK_SECONDS) * 1000),
            )
        consumer = AIOKafkaConsumer(
            topic,
            bootstrap_servers=self._bootstrap_servers,
            group_id=self._group_id,
            enable_auto_commit=False,
            auto_offset_reset="earliest",
            max_poll_interval_ms=poll_interval,
        )
        try:
            await consumer.start()
            if started is not None:
                # 运行入口的启动握手：producer 与主 consumer 真正连接后才可宣布服务就绪。
                started.set()
            while True:
                message = await consumer.getone()
                if stage is None:
                    await self._handle_main(message, producer)
                else:
                    await self._relay(message, producer, stage)
                # offset 表示下一条记录；只确认当前分区，不提交其他分区的处理位置。
                await consumer.commit(
                    {
                        TopicPartition(message.topic, message.partition): message.offset
                        + 1
                    }
                )
        finally:
            # 失败后不继续读；取消处理中任务时也必须释放 Kafka 后台连接和心跳任务。
            await consumer.stop()

    async def _handle_main(
        self, message: ConsumerRecord, producer: AIOKafkaProducer
    ) -> None:
        delivery = _Delivery(message.topic, message.partition, message.offset)
        try:
            delivery = self._read_delivery(message)
            payload = _decode_message(message.value)
        except (TypeError, ValueError) as error:
            await self._forward(
                message,
                producer,
                self._dlq_topic,
                delivery,
                delivery.retry_count,
                error,
            )
            return

        retry_count = delivery.retry_count
        quick_delays = self._retry_policy.backoff_seconds
        delayed = self._retry_policy.delayed_retries
        while True:
            try:
                await self._handler(payload)
                return
            except Exception as error:  # noqa: BLE001 - 传输层统一执行应用异常的重试与 DLQ 策略。
                if not self._is_retryable(error):
                    await self._forward(
                        message, producer, self._dlq_topic, delivery, retry_count, error
                    )
                    return
                if retry_count < len(quick_delays):
                    delay = quick_delays[retry_count]
                    retry_count += 1
                    await asyncio.sleep(delay)
                    continue
                delayed_index = retry_count - len(quick_delays)
                if delayed_index < len(delayed):
                    stage = delayed[delayed_index]
                    await self._forward(
                        message,
                        producer,
                        stage.topic,
                        delivery,
                        retry_count + 1,
                        error,
                        available_at_ms=math.ceil(
                            time.time() * 1000 + stage.delay_seconds * 1000
                        ),
                    )
                    return
                await self._forward(
                    message, producer, self._dlq_topic, delivery, retry_count, error
                )
                return

    async def _relay(
        self, message: ConsumerRecord, producer: AIOKafkaProducer, stage: RetryTopic
    ) -> None:
        delivery = _Delivery(message.topic, message.partition, message.offset)
        try:
            delivery = self._read_delivery(message)
            expected_count = (
                len(self._retry_policy.backoff_seconds)
                + self._retry_policy.delayed_retries.index(stage)
                + 1
            )
            if (
                delivery.available_at_ms is None
                or delivery.retry_count != expected_count
            ):
                raise ValueError("retry message does not match its delayed stage")
            # 不信任外部随意扩大等待期限；队列只承担配置给它的延迟上界。
            if (
                delivery.available_at_ms
                > time.time() * 1000
                + (stage.delay_seconds + _RELAY_SLACK_SECONDS) * 1000
            ):
                raise ValueError("retry deadline exceeds the configured delay")
        except ValueError as error:
            await self._forward(
                message,
                producer,
                self._dlq_topic,
                delivery,
                delivery.retry_count,
                error,
            )
            return

        remaining = max(0, (delivery.available_at_ms - time.time() * 1000) / 1000)
        await asyncio.sleep(remaining)
        # 原 key 与原主分区保持不变；relay 不执行 handler，避免跨 topic 并行构建资源。
        await self._send(
            producer,
            self._topic,
            message,
            partition=delivery.partition,
            headers=message.headers,
        )

    def _read_delivery(self, message: ConsumerRecord) -> _Delivery:
        headers = dict(message.headers)
        if DeliveryHeader.RETRY_COUNT not in headers:
            if _DELIVERY_HEADERS.intersection(headers):
                raise ValueError("retry delivery headers are incomplete")
            return _Delivery(message.topic, message.partition, message.offset)
        try:
            raw_topic = headers[DeliveryHeader.ORIGINAL_TOPIC]
            if not isinstance(raw_topic, bytes):
                raise TypeError("original topic header must be UTF-8 bytes")
            topic = raw_topic.decode("utf-8")
            partition = int(headers[DeliveryHeader.ORIGINAL_PARTITION])
            offset = int(headers[DeliveryHeader.ORIGINAL_OFFSET])
            count = int(headers[DeliveryHeader.RETRY_COUNT])
            available = headers.get(DeliveryHeader.AVAILABLE_AT)
            available_at = int(available) if available is not None else None
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError("invalid retry delivery headers") from error
        max_retries = len(self._retry_policy.backoff_seconds) + len(
            self._retry_policy.delayed_retries
        )
        if (
            topic != self._topic
            or partition < 0
            or offset < 0
            or not 0 <= count <= max_retries
        ):
            raise ValueError("retry delivery identity or count is invalid")
        return _Delivery(topic, partition, offset, count, available_at)

    async def _forward(
        self,
        message: ConsumerRecord,
        producer: AIOKafkaProducer,
        target_topic: str,
        delivery: _Delivery,
        retry_count: int,
        error: Exception,
        *,
        available_at_ms: int | None = None,
    ) -> None:
        headers = [
            (key, value)
            for key, value in message.headers
            if key not in _DELIVERY_HEADERS
        ]
        headers.extend(
            [
                (DeliveryHeader.ORIGINAL_TOPIC, delivery.topic.encode("utf-8")),
                (DeliveryHeader.ORIGINAL_PARTITION, str(delivery.partition).encode()),
                (DeliveryHeader.ORIGINAL_OFFSET, str(delivery.offset).encode()),
                (DeliveryHeader.RETRY_COUNT, str(retry_count).encode()),
                (
                    DeliveryHeader.EXCEPTION_TYPE,
                    f"{type(error).__module__}.{type(error).__name__}".encode(),
                ),
                # 错误文本与 traceback 有界，避免异常对象制造无限增长的消息 headers。
                (DeliveryHeader.EXCEPTION_MESSAGE, str(error)[:4096].encode("utf-8")),
                (
                    DeliveryHeader.EXCEPTION_TRACEBACK,
                    "".join(traceback.format_exception(error))[:8192].encode("utf-8"),
                ),
            ]
        )
        if available_at_ms is not None:
            headers.append((DeliveryHeader.AVAILABLE_AT, str(available_at_ms).encode()))
        # 先确认转发成功，再由 _consume 提交源 offset；发送失败时原记录仍可重放。
        await self._send(producer, target_topic, message, headers=headers)

    async def _send(
        self,
        producer: AIOKafkaProducer,
        topic: str,
        message: ConsumerRecord,
        *,
        headers: list[tuple[str, bytes | None]],
        partition: int | None = None,
    ) -> None:
        # 幂等 producer 不按请求超时过期批次；这里使用有界普通发送与应用幂等。
        # 超时后消息仍可能已送达，因此不确认源记录，允许入口重放产生重复投递。
        await asyncio.wait_for(
            producer.send_and_wait(
                topic,
                value=message.value,
                key=message.key,
                partition=partition,
                headers=headers,
            ),
            timeout=self._delivery_timeout_ms / 1000,
        )


def _decode_message(value: bytes | None) -> dict[str, object]:
    if value is None:
        raise ValueError("Kafka event cannot be a tombstone")
    payload = json.loads(value.decode("utf-8"))
    if isinstance(payload, str):
        # 保留现有上游 JSON 字符串包裹对象的格式，最多解开这一层，不递归猜测协议。
        payload = json.loads(payload)
    if not isinstance(payload, dict):
        raise TypeError("Kafka message payload must be a JSON object")
    return payload
