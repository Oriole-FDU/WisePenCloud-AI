import asyncio
from typing import List, Dict, Any, Optional
from mem0 import Memory

from chat.domain.error_codes import ChatErrorCode
from common.core.exceptions import ServiceException
from common.logger import debug, warn

from chat.domain.entities import ChatMessage, Role
from chat.domain.interfaces import MemoryProvider
from chat.core.config.app_settings import settings


class Mem0Adapter(MemoryProvider):
    def __init__(self):
        self._config = {
            "embedder": {
                "provider": "openai",
                "config": {
                    "model": settings.models.memory.embedding_model,
                    "embedding_dims": settings.models.memory.embedding_dims,
                    "api_key": settings.integrations.common_embedding_provider.api_key,
                    "openai_base_url": settings.integrations.common_embedding_provider.base_url,
                },
            },
            "llm": {
                "provider": "openai",
                "config": {
                    "model": settings.models.memory.llm_model,
                    "api_key": settings.integrations.common_llm_provider.api_key,
                    "openai_base_url": settings.integrations.common_llm_provider.base_url,
                },
            },
            "vector_store": {
                "provider": "qdrant",
                "config": {
                    "collection_name": "wisepen_memories",
                    "url": f"http://{settings.persistence.qdrant.host}:{settings.persistence.qdrant.port}",
                    "api_key": settings.persistence.qdrant.api_key,
                },
            },
        }
        
        try:
            debug("mem0 client initializing.")
            self.client = Memory.from_config(self._config)
            debug("mem0 client initialized.")
        except Exception as e:
            warn("mem0 client initialize failed.", exc=e)
            raise e

    async def search(
            self,
            user_id: str,
            query: str,
            limit: int = 5,
            score_threshold: Optional[float] = None,
    ) -> List[str]:

        def _sync_search():
            kwargs: Dict[str, Any] = {}
            if score_threshold is not None:
                # mem0 2.x 的内置阈值，作用在语义相似度分上（0~1）
                kwargs["threshold"] = score_threshold

            raw_results = self.client.search(
                query,
                filters={"user_id": user_id},
                top_k=limit,
                **kwargs,
            )
            debug("mem0 raw search results returned.", query=query, user_id=user_id, raw_results=raw_results)

            # 兼容 Mem0 返回字典 {"results": [...]} 或直接返回列表的情况
            if isinstance(raw_results, dict):
                results = raw_results.get("results", [])
            else:
                results = raw_results or []

            return [r["memory"] for r in results]

        try:
            return await asyncio.to_thread(_sync_search)
        except Exception as e:
            warn("mem0 search failed.", user_id=user_id, exc=e)
            return []

    async def add_interaction(self, user_id: str, messages: List[ChatMessage]):
        """
        将对话存入长期记忆（Mem0 + Qdrant 向量化）
        """
        # Mem0 需要的是 [{"role": "user", "content": "..."}, ...] 格式
        formatted_msgs = []
        for message in messages:
            # 只摄入用户提问与最终 assistant 回复，中间工具/过程消息不记录
            if message.role in (Role.USER, Role.ASSISTANT):
                formatted_msgs.append({
                    "role": message.role.value, "content": message.content
                })

        def _sync_add():
            try:
                self.client.add(formatted_msgs, user_id=user_id)
            except Exception as e:
                warn("mem0 write failed.", user_id=user_id, exc=e)

        await asyncio.to_thread(_sync_add)

    async def get_all(self, user_id: str) -> List[Dict[str, Any]]:

        def _sync_get_all():
            result = self.client.get_all(filters={"user_id": user_id})
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

