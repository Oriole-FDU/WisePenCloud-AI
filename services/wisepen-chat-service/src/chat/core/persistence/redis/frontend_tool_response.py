import json

from redis.asyncio import Redis

from chat.core.config.app_settings import settings
from chat.core.persistence.redis.base import RedisRepository


class RedisFrontendToolResponseRepository(RedisRepository):
    """保存 session 正在等待前端结果的标记"""

    def __init__(self, *, redis_client: Redis) -> None:
        super().__init__(redis_client=redis_client)
        # 等待标记只需活过前端等待超时
        self.state_ttl_seconds = int(settings.chat_turn.frontend_response_timeout_seconds) + 30 # 30s 是给提交和超时之间的竞态留的缓冲

    @staticmethod
    def _key(session_id: str) -> str:
        return f"chat:session:{session_id}:frontend_tool_response"

    async def register_wait(
        self,
        session_id: str,
        expected_client_tool_call_ids: list[str],
        expected_approval_tool_call_ids: list[str],
    ) -> None:
        """写入等待标记"""
        value = json.dumps({
            "expected_client_tool_call_ids": expected_client_tool_call_ids,
            "expected_approval_tool_call_ids": expected_approval_tool_call_ids,
        }, ensure_ascii=False)
        await self._redis.set(self._key(session_id), value, ex=self.state_ttl_seconds)

    async def get_wait(self, session_id: str) -> dict | None:
        """读取等待标记内容"""
        raw = await self._redis.get(self._key(session_id))
        return json.loads(raw) if raw else None

    async def claim(self, session_id: str) -> bool:
        """原子认领，True 表示这次调用拿走了等待权，False 表示已经被别人拿走了"""
        return bool(await self._redis.delete(self._key(session_id)))
