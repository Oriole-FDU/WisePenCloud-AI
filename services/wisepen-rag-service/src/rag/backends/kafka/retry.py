"""Kafka 重试预算与消息追踪 headers，不承载应用发布状态。"""

from dataclasses import dataclass, field
from enum import StrEnum


@dataclass(frozen=True, slots=True)
class RetryTopic:
    topic: str
    delay_seconds: float  # 从失败转发时开始计时，到期后回到原主 topic。


@dataclass(frozen=True, slots=True, kw_only=True)
class RetryPolicy:
    backoff_seconds: list[float] = field(default_factory=lambda: [1, 2, 4])
    delayed_retries: list[RetryTopic] = field(default_factory=list)


class DeliveryHeader(StrEnum):
    """保留原消息位置和重试链；rag- 前缀由传输层拥有。"""

    ORIGINAL_TOPIC = "rag-original-topic"
    ORIGINAL_PARTITION = "rag-original-partition"
    ORIGINAL_OFFSET = "rag-original-offset"
    RETRY_COUNT = "rag-retry-count"
    AVAILABLE_AT = "rag-available-at-ms"
    EXCEPTION_TYPE = "rag-exception-type"
    EXCEPTION_MESSAGE = "rag-exception-message"
    EXCEPTION_TRACEBACK = "rag-exception-traceback"
