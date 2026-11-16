import json
from typing import List

from redis.asyncio import Redis

from chat.core.persistence.redis.base import RedisRepository
from chat.domain.entities import ChatMessage
from chat.domain.repositories import HotContextRepository


class RedisHotContext(RedisRepository, HotContextRepository):
    def __init__(self, *, redis_client: Redis) -> None:
        super().__init__(redis_client=redis_client)
        self.ttl = 3600 * 24 * 7  # 会话上下文保留一个周

    def _get_key(self, session_id: str) -> str:
        return f"wisepen:chat:hot_context:{session_id}"

    def _serialize(self, messages: List[ChatMessage]) -> List[str]:
        serialized = []
        for msg in messages:
            data = msg.model_dump(mode="json", exclude={"id"})
            # 热上下文保留原文，跨回合 fork 时仍需遵守原消息的持久化策略。
            if msg.persisted_output_placeholder is not None:
                data["persisted_output_placeholder"] = msg.persisted_output_placeholder
            serialized.append(json.dumps(data, ensure_ascii=False))
        return serialized

    async def append_messages(self, session_id: str, messages: List[ChatMessage], max_length: int = 50) -> None:
        key = self._get_key(session_id)
        serialized = self._serialize(messages)
        async with self._redis.pipeline(transaction=True) as pipe:
            await pipe.rpush(key, *serialized)
            await pipe.ltrim(key, -max_length, -1)
            await pipe.expire(key, self.ttl)
            await pipe.execute()

    async def get_recent_context(self, session_id: str) -> List[ChatMessage]:
        key = self._get_key(session_id)
        raw_msgs = await self._redis.lrange(key, 0, -1)
        return [ChatMessage(**json.loads(msg)) for msg in raw_msgs]

    async def load_messages(self, session_id: str, messages: List[ChatMessage]) -> None:
        """用给定消息重建热缓存；传入空列表表示清空。"""
        key = self._get_key(session_id)
        if not messages:
            await self._redis.delete(key)
            return
        serialized = self._serialize(messages)
        async with self._redis.pipeline(transaction=True) as pipe:
            await pipe.delete(key)
            await pipe.rpush(key, *serialized)
            await pipe.expire(key, self.ttl)
            await pipe.execute()
