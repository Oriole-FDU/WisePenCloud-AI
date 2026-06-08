from typing import Any, Dict, List, Optional

import redis.asyncio as redis
from redis.exceptions import ResponseError

from chat.application.rag.enums import ResourceKind
from chat.application.rag.runtime.indexing.models import RagIndexMessage
from chat.application.rag.runtime.persistence.interfaces import (
    RagIndexingQueueRepository,
    RagQueuedIndexMessage,
)
from chat.core.config.app_settings import settings

_RAG_INDEXING_STREAM = "wisepen:rag:indexing"
_RAG_INDEXING_DEAD_STREAM = "wisepen:rag:indexing:dead"


class RedisRagIndexingQueue(RagIndexingQueueRepository):
    """Redis RAG 索引队列。

    - 使用 Redis Stream 保存后台索引消息。
    - publish 使用 XADD 往 Stream 追加消息。
    - read_batch 使用 XREADGROUP 从消费者组读取消息。
    - ack 使用 XACK 确认消息处理完成。
    """

    def __init__(self) -> None:
        # decode_responses=True:
        # - Redis 返回 str，而不是 bytes。
        # - 这样 _deserialize() 里不需要手动 decode。
        """初始化对象依赖。"""
        self.redis = redis.from_url(settings.REDIS_URL, decode_responses=True)

    async def publish(self, message: RagIndexMessage) -> None:
        """发布索引消息。

        - 底层使用 XADD。
        - 每次写入都会生成一个 Redis Stream message_id。
        - message_id 由 Redis 生成，不由业务层控制。
        """

        await self.redis.xadd(
            name=_RAG_INDEXING_STREAM,
            fields={
                "user_id": message.user_id,
                "resource_kind": message.resource_kind.value,
                "resource_id": message.resource_id,
                "expected_version": str(message.expected_version),
                "pipeline_version": message.pipeline_version,
                "target_index_version": message.target_index_version,
                "priority": str(message.priority),
                "attempts": "0",
            },
        )

    async def ensure_consumer_group(self, consumer_group: str) -> None:
        """确保 Redis Stream consumer group 存在。

        - consumer group 用于多 worker 协同消费同一个 Stream。
        - 同一个 group 内，一条消息通常只会分配给一个 consumer。
        - id="0" 表示从 Stream 最早的消息开始建立消费组。
        - mkstream=True 表示 Stream 不存在时自动创建。
        - BUSYGROUP 表示 group 已存在，属于正常情况，直接忽略。
        """

        try:
            await self.redis.xgroup_create(
                name=_RAG_INDEXING_STREAM,
                groupname=consumer_group,
                id="0",
                mkstream=True,
            )
        except ResponseError as e:
            if "BUSYGROUP" not in str(e):
                raise

    async def read_batch(
        self,
        consumer_group: str,
        consumer_name: str,
        count: int = 10,
        block_ms: int = 5000,
    ) -> List[RagQueuedIndexMessage]:
        """批量读取索引消息。

        - streams={stream: ">"} 表示只读取尚未投递给当前 group 的新消息。
        - 已读取但未 ack 的消息会留在 Redis Pending Entries List 中。

        Args:
        - consumer_group: 消费者组名称。
        - consumer_name: 当前消费者名称。
        - count: 单次最多读取消息数。
        - block_ms: 没有消息时的阻塞等待时间，单位毫秒。

        Returns:
        - 反序列化后的队列消息列表。
        """

        raw_streams = await self.redis.xreadgroup(
            groupname=consumer_group,
            consumername=consumer_name,
            streams={_RAG_INDEXING_STREAM: ">"},
            count=count,
            block=block_ms,
        )

        # raw_streams 结构：
        # [
        #   (
        #     "wisepen:rag:indexing",
        #     [
        #       ("message-id-1", {"field": "value"}),
        #       ("message-id-2", {"field": "value"}),
        #     ],
        #   )
        # ]
        return [
            RagQueuedIndexMessage(
                message_id=msg_id,
                message=self._deserialize(fields),
                attempts=int(fields.get("attempts", "0")),
            )
            for _, raw_messages in (raw_streams or [])
            for msg_id, fields in raw_messages
        ]

    async def ack(
        self,
        message_id: str,
        consumer_group: str,
    ) -> None:
        """确认消息处理完成。

        - worker成功处理消息后 ack，ack 后消息会从该 consumer group 的 pending 列表中移除。
        """

        await self.redis.xack(
            _RAG_INDEXING_STREAM,
            consumer_group,
            message_id,
        )

    async def close(self) -> None:
        """关闭当前流程。"""
        await self.redis.aclose()

    async def read_stale_pending_batch(
            self,
            consumer_group: str,
            consumer_name: str,
            min_idle_ms: int = 60000,
            count: int = 10,
    ) -> List[RagQueuedIndexMessage]:
        """抢回长时间未 ack 的 pending 消息。

        - 使用 XAUTOCLAIM 从 Pending Entries List 中领取超时消息。
        - 用于 worker 崩溃 / 网络中断后的消息恢复。
        - min_idle_ms 表示 pending 消息空闲多久后可以被重新领取。
        - start_id="0-0" 表示从 pending 列表头部开始扫描。
        """
        raw_result = await self.redis.xautoclaim(
            name=_RAG_INDEXING_STREAM,
            groupname=consumer_group,
            consumername=consumer_name,
            min_idle_time=min_idle_ms,
            start_id="0-0",
            count=count,
        )

        return [
            RagQueuedIndexMessage(
                message_id=msg_id,
                message=self._deserialize(fields),
                attempts=int(fields.get("attempts", "0")),
            )
            for msg_id, fields in raw_result[1]
        ]

    async def ack_many(
        self,
        message_ids: List[str],
        consumer_group: str,
    ) -> None:
        """批量确认消息处理完成。"""

        if message_ids:
            await self.redis.xack(
                _RAG_INDEXING_STREAM,
                consumer_group,
                *message_ids,
            )

    async def mark_indexing(
        self,
        user_id: str,
        resource_id: str,
        index_version: str,
    ) -> Optional[Any]:
        """临时空实现，避免 RAG 队列因抽象方法未实现阻塞主流程。"""
        return None

    async def mark_index_success(
        self,
        user_id: str,
        resource_id: str,
        index_version: str,
    ) -> Optional[Any]:
        """临时空实现，避免 RAG 队列因抽象方法未实现阻塞主流程。"""
        return None

    async def mark_index_failed(
        self,
        user_id: str,
        resource_id: str,
        index_version: str,
        error: str,
    ) -> Optional[Any]:
        """临时空实现，避免 RAG 队列因抽象方法未实现阻塞主流程。"""
        return None

    async def handle_failure(
        self,
        queued_message: RagQueuedIndexMessage,
        consumer_group: str,
        error: str,
        max_attempts: int,
    ) -> None:
        """失败消息超过阈值进入死信队列，否则重新入主队列。"""
        next_attempts = queued_message.attempts + 1
        target_stream = (
            _RAG_INDEXING_DEAD_STREAM
            if next_attempts >= max_attempts
            else _RAG_INDEXING_STREAM
        )

        await self.redis.xadd(
            name=target_stream,
            fields={
                **self._serialize(queued_message.message),
                "attempts": str(next_attempts),
                "failed_message_id": queued_message.message_id,
                "last_error": error[:1000],
            },
        )
        await self.ack(
            message_id=queued_message.message_id,
            consumer_group=consumer_group,
        )

    def _deserialize(self, fields: Dict[str, Any]) -> RagIndexMessage:
        """反序列化 Redis Stream 字段。

        - Redis 字段是字符串。
        - resource_kind 必须精确匹配 ResourceKind。
        - expected_version / priority 必须是合法整数字符串。
        """

        return RagIndexMessage(
            user_id=fields["user_id"],
            resource_kind=ResourceKind(fields["resource_kind"]),
            resource_id=fields["resource_id"],
            expected_version=int(fields["expected_version"]),
            pipeline_version=fields["pipeline_version"],
            target_index_version=fields["target_index_version"],
            priority=int(fields["priority"]),
        )

    def _serialize(self, message: RagIndexMessage) -> Dict[str, str]:
        return {
            "user_id": message.user_id,
            "resource_kind": message.resource_kind.value,
            "resource_id": message.resource_id,
            "expected_version": str(message.expected_version),
            "pipeline_version": message.pipeline_version,
            "target_index_version": message.target_index_version,
            "priority": str(message.priority),
        }
