import asyncio
from typing import List, Dict, Any, Optional
from time import monotonic
from mem0 import Memory

from chat.domain.error_codes import ChatErrorCode
from common.core.exceptions import ServiceException
from common.logger import debug, warn

from chat.domain.entities import ChatMessage, Role
from chat.domain.interfaces import MemoryProvider
from chat.core.config.app_settings import settings


class Mem0Adapter(MemoryProvider):
    INITIALIZATION_TIMEOUT_SECONDS = 10
    OPERATION_TIMEOUT_SECONDS = 30
    RETRY_COOLDOWN_SECONDS = 30
    MAX_INFLIGHT_OPERATIONS = 4

    def __init__(self):
        self._client = None
        self._initialization_task = None
        self._initialization_lock = asyncio.Lock()
        self._retry_after = 0.0
        self._operations = set()

    def _create_client(self):
        required = (
            "MEMORY_LLM_MODEL", "MEMORY_EMBEDDING_MODEL", "QDRANT_HOST",
            "LLM_API_KEY", "LLM_BASE_URL",
        )
        if any(not getattr(settings, name) for name in required):
            raise ServiceException(
                ChatErrorCode.MEMORY_UNAVAILABLE,
                custom_msg="长期记忆配置不完整，请检查记忆模型与 Qdrant 配置",
            )
        config = {
            "embedder": {
                "provider": "openai",
                "config": {
                    "model": settings.MEMORY_EMBEDDING_MODEL,
                    "api_key": settings.LLM_API_KEY,
                    "openai_base_url": settings.LLM_BASE_URL,
                },
            },
            "llm": {
                "provider": "openai",
                "config": {
                    "model": settings.MEMORY_LLM_MODEL,
                    "api_key": settings.LLM_API_KEY,
                    "openai_base_url": settings.LLM_BASE_URL,
                },
            },
            "vector_store": {
                "provider": "qdrant",
                "config": {
                    "collection_name": "wisepen_memories",
                    "url": f"http://{settings.QDRANT_HOST}:{settings.QDRANT_PORT}",
                    "api_key": settings.QDRANT_PASSWORD,
                },
            },
        }
        # Mem0 搜索默认不重排，无需创建未使用的重排客户端。
        client = Memory.from_config(config)
        # 使用 SDK 的请求超时并关闭自动重试，限制等待超时后仍在执行的线程。
        client.embedding_model.client = client.embedding_model.client.with_options(timeout=10, max_retries=0)
        client.llm.client = client.llm.client.with_options(timeout=10, max_retries=0)
        return client

    @staticmethod
    def _consume_exception(task):
        # 调用者超时或取消后，后台线程仍可能结束；回收异常，避免未读取的 Task 异常。
        if not task.cancelled():
            task.exception()

    async def _initialize(self):
        try:
            debug("mem0 client initializing.")
            self._client = await asyncio.to_thread(self._create_client)
            debug("mem0 client initialized.")
            return self._client
        except Exception as e:
            self._retry_after = monotonic() + self.RETRY_COOLDOWN_SECONDS
            warn("mem0 client initialize failed.", error_type=type(e).__name__)
            if isinstance(e, ServiceException):
                raise
            raise ServiceException(ChatErrorCode.MEMORY_UNAVAILABLE) from None

    async def _get_client(self):
        if self._client is not None:
            return self._client
        async with self._initialization_lock:
            if self._initialization_task is None or self._initialization_task.done():
                if monotonic() < self._retry_after:
                    raise ServiceException(ChatErrorCode.MEMORY_UNAVAILABLE)
                self._initialization_task = asyncio.create_task(self._initialize())
                self._initialization_task.add_done_callback(self._consume_exception)
            task = self._initialization_task
        try:
            # 超时不取消共享初始化，也不启动另一条初始化线程。
            return await asyncio.wait_for(asyncio.shield(task), self.INITIALIZATION_TIMEOUT_SECONDS)
        except TimeoutError:
            raise ServiceException(ChatErrorCode.MEMORY_TIMEOUT) from None

    async def _run(self, operation):
        client = await self._get_client()
        # 同步 SDK 无法中断线程。保留超时任务的名额，避免重试无限堆积后台调用。
        if len(self._operations) >= self.MAX_INFLIGHT_OPERATIONS:
            raise ServiceException(ChatErrorCode.MEMORY_UNAVAILABLE)
        task = asyncio.create_task(asyncio.to_thread(operation, client))
        self._operations.add(task)
        task.add_done_callback(self._operations.discard)
        task.add_done_callback(self._consume_exception)
        try:
            return await asyncio.wait_for(asyncio.shield(task), self.OPERATION_TIMEOUT_SECONDS)
        except TimeoutError:
            raise ServiceException(ChatErrorCode.MEMORY_TIMEOUT) from None
        except ServiceException:
            raise
        except Exception as e:
            warn("mem0 operation failed.", operation=operation.__name__, error_type=type(e).__name__)
            raise ServiceException(ChatErrorCode.MEMORY_OPERATION_FAILED) from None

    async def search(
            self,
            user_id: str,
            query: str,
            limit: int = 5,
            score_threshold: Optional[float] = None,
    ) -> List[str]:

        def _sync_search(client):
            raw_results = client.search(query, user_id=user_id, limit=limit)

            # 兼容 Mem0 返回字典 {"results": [...]} 或直接返回列表的情况
            if isinstance(raw_results, dict):
                results = raw_results.get("results", [])
            else:
                results = raw_results or []

            if not results:
                return []
            if score_threshold is not None:
                # 按分数阈值过滤，忽略 limit 参数
                return [r["memory"] for r in results if r.get("rerank_score") >= score_threshold]
            return [r["memory"] for r in results]

        return await self._run(_sync_search)

    async def add_interaction(self, user_id: str, messages: List[ChatMessage]):
        """
        将对话存入长期记忆（Mem0 + Qdrant 向量化）
        """
        # Mem0 需要的是 [{"role": "user", "content": "..."}, ...] 格式
        formatted_msgs = []
        for message in messages:
            # 只有 Role.USER Message 才应存入长期记忆
            if message.role == Role.USER:
                formatted_msgs.append({
                    "role": message.role.value, "content": message.content
                })

        if not formatted_msgs:
            return

        def _sync_add(client):
            client.add(formatted_msgs, user_id=user_id)

        await self._run(_sync_add)

    async def get_all(self, user_id: str) -> List[Dict[str, Any]]:

        def _sync_get_all(client):
            result = client.get_all(user_id=user_id)
            # Mem0 返回格式: {"results": [...]} 或直接 list
            if isinstance(result, dict):
                return result.get("results", [])
            return result or []

        return await self._run(_sync_get_all)

    async def delete_memory(self, memory_id: str, user_id: str) -> None:

        def _sync_verify_and_delete(client):
            memory = client.get(memory_id)
            if not memory:
                return
            owner_id = memory.get("user_id")
            if owner_id != user_id:
                raise ServiceException(ChatErrorCode.MEMORY_NOT_FOUND)
            client.delete(memory_id)

        await self._run(_sync_verify_and_delete)

    async def delete_all_for_user(self, user_id: str) -> None:

        def _sync_delete_all(client):
            client.delete_all(user_id=user_id)

        await self._run(_sync_delete_all)
