"""S2 的网络传输与平台发送配额，供在线适配器和离线步骤共同使用。"""

import asyncio

import httpx
from redis.asyncio import Redis

_PERMIT = """
local clock = redis.call('TIME')
local now = clock[1] * 1000 + math.floor(clock[2] / 1000)
local previous = tonumber(redis.call('GET', KEYS[1]) or '0')
local delay = previous + 1000 - now
if delay > 0 then return delay end
redis.call('SET', KEYS[1], now, 'PX', 2000)
return 0
"""


class S2Transport:
    def __init__(self, http: httpx.AsyncClient, redis: Redis, api_key: str):
        self.http, self.redis, self._api_key = http, redis, api_key
        # HTTPX 默认携带 User-Agent；用户实测要求 S2 专用客户端不发送该头。
        self.http.headers.pop("User-Agent", None)

    async def request(self, method: str, path: str, **kwargs):
        # 只领取当前发送许可，不预订未来槽位；取消等待不会残留排队任务。
        # Redis 不可用时失败，不能绕过所有进程共用的平台速率限制。
        while True:
            delay = await self.redis.eval(_PERMIT, 1, "scholar:s2:send-permit")
            if delay == 0:
                break
            await asyncio.sleep(delay / 1000)
        request = self.http.build_request(
            method,
            f"https://api.semanticscholar.org/{path}",
            headers={"x-api-key": self._api_key},
            **kwargs,
        )
        response = await self.http.send(request)
        response.raise_for_status()
        return response.json()
