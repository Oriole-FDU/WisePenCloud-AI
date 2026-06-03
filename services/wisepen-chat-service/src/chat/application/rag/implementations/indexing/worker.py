import asyncio
from typing import List

from chat.application.rag.domain.ports import (
    RagIndexingQueueRepository,
    RagQueuedIndexMessage,
)
from .processor import RagIndexMessageProcessor


class RagIndexWorker:
    """RAG 索引 Worker。

    - 优先抢回 Redis pending 中超时未 ACK 的消息。
    - 没有 stale pending 时，再读取新消息。
    - 单批处理中，已经成功处理的消息会批量 ACK。
    - 当前失败消息和后续未处理消息不会 ACK。
    - 未 ACK 消息后续由 Redis pending reclaim 机制重新投递。
    """

    def __init__(
        self,
        indexing_queue_repository: RagIndexingQueueRepository,
        processor: RagIndexMessageProcessor,
        consumer_group: str,
        consumer_name: str,
        batch_size: int = 10,
        block_ms: int = 5000,
        pending_min_idle_ms: int = 60000,
        error_sleep_seconds: float = 1.0,
    ) -> None:
        """初始化 Worker。

        Args:
            indexing_queue_repository: 索引队列仓储。
            processor: 索引消息处理器。
            consumer_group: 消费者组名。
            consumer_name: 消费者名。
            batch_size: 每次拉取的消息数。
            block_ms: 队列为空时阻塞等待的超时时间（毫秒）。
            pending_min_idle_ms: 判定 stale pending 的最小空闲时间（毫秒）。
            error_sleep_seconds: 处理异常时的休眠间隔（秒）。
        """
        self._indexing_queue_repository = indexing_queue_repository
        self._processor = processor
        self._consumer_group = consumer_group
        self._consumer_name = consumer_name
        self._batch_size = batch_size
        self._block_ms = block_ms
        self._pending_min_idle_ms = pending_min_idle_ms
        self._error_sleep_seconds = error_sleep_seconds

    async def process_once(self) -> int:
        """处理一批索引消息。

        流程：
        1. 先读取 stale pending 消息（超时未 ACK 的遗留消息）
        2. stale pending 为空时，再读取新消息
        3. 逐条处理消息，已成功的记录到 ack 列表
        4. 处理失败时：先 ACK 已成功消息，再抛出异常
        5. 全部成功时：批量 ACK

        Returns:
            本轮读取到的消息数量（包括成功和失败的消息）。
        """
        queued_messages = await self._read_recoverable_batch()
        if not queued_messages:
            return 0

        ack_message_ids: List[str] = []

        try:
            for queued_message in queued_messages:
                await self._processor.process(queued_message.message)
                ack_message_ids.append(queued_message.message_id)
        except Exception:
            if ack_message_ids:
                await self._indexing_queue_repository.ack_many(
                    message_ids=ack_message_ids,
                    consumer_group=self._consumer_group,
                )
            raise

        if ack_message_ids:
            await self._indexing_queue_repository.ack_many(
                message_ids=ack_message_ids,
                consumer_group=self._consumer_group,
            )

        return len(queued_messages)

    async def start_loop(self) -> None:
        """持续消费索引消息。

        - 适合后台 worker 进程运行。
        - process_once 抛出异常时短暂休眠后继续。
        - 未 ACK 的失败消息会留在 Redis pending 中，等待后续 reclaim。
        """
        while True:
            try:
                await self.process_once()
            except Exception:
                await asyncio.sleep(self._error_sleep_seconds)

    async def _read_recoverable_batch(self) -> List[RagQueuedIndexMessage]:
        """读取可恢复的消息批次。

        原位调度策略：
        1. 先回收超时未 ACK 的僵死消息（stale pending）
        2. 没有 stale pending 时，再读取队列中的新消息
        """
        pending_messages = (
            await self._indexing_queue_repository.read_stale_pending_batch(
                consumer_group=self._consumer_group,
                consumer_name=self._consumer_name,
                min_idle_ms=self._pending_min_idle_ms,
                count=self._batch_size,
            )
        )
        if pending_messages:
            return pending_messages

        return await self._indexing_queue_repository.read_batch(
            consumer_group=self._consumer_group,
            consumer_name=self._consumer_name,
            count=self._batch_size,
            block_ms=self._block_ms,
        )