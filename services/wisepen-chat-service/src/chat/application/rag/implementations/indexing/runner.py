from chat.application.rag.implementations.indexing.worker import RagIndexWorker
from chat.application.rag.implementations.persistence.redis.indexing_queue import RedisRagIndexingQueue


class RagIndexWorkerRunner:
    """RAG 索引 Worker 启动器。

    - 负责启动前确保 Redis consumer group 存在。
    - 负责启动 Worker 主循环。
    - 不负责具体索引逻辑。
    - 不负责应用生命周期管理。
    """

    def __init__(
        self,
        indexing_queue: RedisRagIndexingQueue,
        worker: RagIndexWorker,
        consumer_group: str,
    ) -> None:
        """初始化 Worker 启动器。

        Args:
            indexing_queue: Redis 索引队列实例。
            worker: RAG 索引 Worker 实例。
            consumer_group: 消费者组名，用于确保消费组存在。
        """
        self._indexing_queue = indexing_queue
        self._worker = worker
        self._consumer_group = consumer_group

    async def start(self) -> None:
        """启动索引 Worker 长循环。

        先确保 Redis consumer group 存在，然后进入 Worker 主循环。
        Worker 主循环会持续从队列中拉取消息并处理，直到收到 CancelledError。
        """

        await self._indexing_queue.ensure_consumer_group(self._consumer_group)
        await self._worker.start_loop()