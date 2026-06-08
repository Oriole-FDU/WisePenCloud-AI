import json
import redis.asyncio as redis
from typing import List
from chat.domain.repositories import HotContextRepository
from chat.domain.entities import ChatMessage
from chat.core.config.app_settings import settings


class RedisHotContext(HotContextRepository):
    def __init__(self):
        self.redis = redis.from_url(settings.REDIS_URL, decode_responses=True)
        self.ttl = 3600 * 24  # 会话上下文保留 24 小时

    def _get_key(self, session_id: str) -> str:
        return f"wisepen:chat:hot_context:{session_id}"

    def _serialize(self, messages: List[ChatMessage]) -> List[str]:
        return [
            json.dumps(msg.model_dump(mode="json", exclude={"id"}), ensure_ascii=False)
            for msg in messages
        ]

    async def append_messages(self, session_id: str, messages: List[ChatMessage], max_length: int = 50) -> None:
        key = self._get_key(session_id)
        serialized = self._serialize(messages)
        async with self.redis.pipeline(transaction=True) as pipe:
            await pipe.rpush(key, *serialized)
            await pipe.ltrim(key, -max_length, -1)
            await pipe.expire(key, self.ttl)
            await pipe.execute()

    async def get_recent_context(self, session_id: str) -> List[ChatMessage]:
        key = self._get_key(session_id)
        raw_msgs = await self.redis.lrange(key, 0, -1)
        return [ChatMessage(**json.loads(msg)) for msg in raw_msgs]

    async def load_messages(self, session_id: str, messages: List[ChatMessage]) -> None:
        """将历史明细批量写入 Redis，重建热缓存。"""
        if not messages:
            return
        key = self._get_key(session_id)
        serialized = self._serialize(messages)
        async with self.redis.pipeline(transaction=True) as pipe:
            await pipe.delete(key)
            await pipe.rpush(key, *serialized)
            await pipe.expire(key, self.ttl)
            await pipe.execute()


