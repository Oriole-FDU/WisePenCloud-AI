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
        if limit == 0:
            return []

        def _sync_search():
            raw_results = self.client.search(
                query,
                filters={"user_id": user_id},
                top_k=limit,
                threshold=score_threshold if score_threshold is not None else 0.0,
                rerank=False,
            )
            results = self._parse_results(raw_results)
            debug("mem0 search completed.", user_id=user_id, result_count=len(results))
            return [r["memory"] for r in results]

        try:
            return await asyncio.to_thread(_sync_search)
        except ServiceException:
            raise
        except Exception as e:
            warn("mem0 search failed.", user_id=user_id, error_type=type(e).__name__)
            raise ServiceException(ChatErrorCode.MEMORY_OPERATION_FAILED) from None

    @staticmethod
    def _parse_results(result: Any) -> List[Dict[str, Any]]:
        # 按锁定的 Mem0 2.x 契约读取，格式错误不能当成正常零命中。
        if not isinstance(result, dict) or not isinstance(result.get("results"), list):
            raise ServiceException(ChatErrorCode.MEMORY_RESPONSE_INVALID)
        items = result["results"]
        if any(
            not isinstance(item, dict)
            or not isinstance(item.get("id"), str)
            or not isinstance(item.get("memory"), str)
            or (item.get("metadata") is not None and not isinstance(item["metadata"], dict))
            for item in items
        ):
            raise ServiceException(ChatErrorCode.MEMORY_RESPONSE_INVALID)
        return items

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

        def _sync_add():
            try:
                self.client.add(formatted_msgs, user_id=user_id)
            except Exception as e:
                warn("mem0 write failed.", user_id=user_id, exc=e)

        await asyncio.to_thread(_sync_add)

    async def get_all(self, user_id: str, limit: int = 100) -> List[Dict[str, Any]]:

        def _sync_get_all():
            result = self.client.get_all(filters={"user_id": user_id}, top_k=limit)
            return self._parse_results(result)

        try:
            return await asyncio.to_thread(_sync_get_all)
        except ServiceException:
            raise
        except Exception as e:
            warn("mem0 list failed.", user_id=user_id, error_type=type(e).__name__)
            raise ServiceException(ChatErrorCode.MEMORY_OPERATION_FAILED) from None

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
