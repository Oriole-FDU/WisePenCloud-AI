from typing import List

from redis.asyncio import Redis

from chat.core.config.app_settings import settings
from chat.core.persistence.redis.base import RedisRepository
from chat.domain.repositories import RedirectQueueRepository


_DRAIN_ALL_SCRIPT = """
local items = redis.call('LRANGE', KEYS[1], 0, -1)
redis.call('DEL', KEYS[1])
return items
"""


class RedisChatTurnQueue(RedisRepository, RedirectQueueRepository):
    """待注入当前 turn 的用户引导消息队列（FIFO，按序注入）。"""

    def __init__(self, *, redis_client: Redis) -> None:
        super().__init__(redis_client=redis_client)
        self.ttl_seconds = int(settings.chat_turn.active_ttl_seconds)

    @staticmethod
    def _key(user_id: str, session_id: str) -> str:
        return f"chat:session:{user_id}:{session_id}:redirect_queue"

    async def enqueue(self, user_id: str, session_id: str, query: str) -> None:
        key = self._key(user_id, session_id)
        await self._redis.rpush(key, query)
        await self._redis.expire(key, self.ttl_seconds)

    async def drain_all(self, user_id: str, session_id: str) -> List[str]:
        key = self._key(user_id, session_id)
        items = await self._redis.eval(_DRAIN_ALL_SCRIPT, 1, key)
        return list(items)
