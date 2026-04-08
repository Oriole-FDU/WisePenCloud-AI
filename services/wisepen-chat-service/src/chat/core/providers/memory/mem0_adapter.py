import asyncio
from typing import List, Dict, Any, Optional
from mem0 import Memory

from chat.domain.error_codes import ChatErrorCode
from common.core.exceptions import ServiceException
from common.logger import log_fail, log_debug

from chat.domain.entities import ChatMessage
from chat.domain.interfaces import MemoryProvider
from chat.core.config.app_settings import settings


class Mem0Adapter(MemoryProvider):
    def __init__(self):
        self._config = {
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
            "reranker": {
                "provider": "zero_entropy",
                "config": {
                    "model": settings.MEMORY_RERANKER_ZE_MODEL,
                    "api_key": settings.ZERO_ENTROPY_API_KEY,
                    "top_k": 5
                }
            },
            "vector_store": {
                "provider": "qdrant",
                "config": {
                    "collection_name": "wisepen_memories",
<<<<<<< HEAD
                    "host": settings.QDRANT_HOST,
                    "port": settings.QDRANT_PORT,
=======
                    "url": f"http://{settings.QDRANT_HOST}:{settings.QDRANT_PORT}",
                    "api_key": settings.QDRANT_PASSWORD,
>>>>>>> 1474d01 (fix:修正计费信息获取逻辑，修复 mem0 及 prompt 优化)
                },
            },
        }
        self._client = None

    def _get_client(self) -> Optional[Memory]:
        if self._client is None:
            log_debug("Lazy initializing Mem0 Client")
            try:
                self._client = Memory.from_config(self._config)
            except Exception as e:
                log_fail("初始化 Mem0 Client", e)
                return None
        return self._client

    async def search(
            self,
            user_id: str,
            query: str,
            limit: int = 5,
            score_threshold: Optional[float] = None,
    ) -> List[str]:

        def _sync_search():
            client = self._get_client()
            if client is None:
                return []
            
            raw_results = client.search(query, user_id=user_id, limit=limit)
            log_debug(f"Raw Results from Mem0", query=query, user_id=user_id, raw_results=raw_results)

            # 兼容 Mem0 返回字典 {"results": [...]} 或直接返回列表的情况
            if isinstance(raw_results, dict):
                results = raw_results.get("results", [])
            else:
                results = raw_results or []

            if not results:
                return []
            if score_threshold is not None:
                # 按分数阈值过滤，忽略 limit 参数
                return [r["memory"] for r in results if r.get("rerank_score", 0) >= score_threshold]
            return [r["memory"] for r in results]

        try:
            return await asyncio.to_thread(_sync_search)
        except Exception as e:
            log_fail("长期记忆检索", e, user=user_id)
            return []

    async def add_interaction(self, user_id: str, messages: List[ChatMessage]):
        """
        将对话存入长期记忆（Mem0 + Qdrant 向量化）。
        """
        # Mem0 需要的是 [{"role": "user", "content": "..."}, ...] 格式
        formatted_msgs = [
            {"role": msg.role.value, "content": msg.content}
            for msg in messages
        ]

        def _sync_add():
<<<<<<< HEAD
            client = self._get_client()
            if client is None:
                log_fail("长期记忆写入", "Mem0 客户端未成功初始化，跳过写入")
                return
            
            try:
                client.add(formatted_msgs, user_id=user_id)
=======
            try:
                self.client.add(formatted_msgs, user_id=user_id)
>>>>>>> 1474d01 (fix:修正计费信息获取逻辑，修复 mem0 及 prompt 优化)
            except Exception as e:
                log_fail("长期记忆写入异常", e, user=user_id)

        await asyncio.to_thread(_sync_add)

    async def get_all(self, user_id: str) -> List[Dict[str, Any]]:

        def _sync_get_all():
            result = self.client.get_all(user_id=user_id)
            # Mem0 返回格式: {"results": [...]} 或直接 list
            if isinstance(result, dict):
                return result.get("results", [])
            return result or []

        return await asyncio.to_thread(_sync_get_all)

    async def delete_memory(self, memory_id: str, user_id: str) -> None:

        def _sync_verify_and_delete():
            memory = self.client.get(memory_id)
            if not memory:
                return
            owner_id = memory.get("user_id")
            if owner_id != user_id:
                raise ServiceException(ChatErrorCode.MEMORY_NOT_FOUND)
            self.client.delete(memory_id)

        await asyncio.to_thread(_sync_verify_and_delete)

    async def delete_all_for_user(self, user_id: str) -> None:

        def _sync_delete_all():
            self.client.delete_all(user_id=user_id)

        await asyncio.to_thread(_sync_delete_all)
