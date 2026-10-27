"""S2 在线与离线共用 FIFO 和全局 1 RPS；每个 HTTP 请求只占一个等待项。"""

from __future__ import annotations

import asyncio
from uuid import uuid4

import httpx
from redis.asyncio import Redis

from scholar.domain.interfaces.provider_error import ProviderBusy, ProviderError
from scholar.domain.interfaces.provider_execution import provider_queued_ahead

# 使用 Redis 时钟和原子脚本，避免实例时钟差异改变 FIFO 或发送间隔。
# 租约只覆盖等待期；崩溃进程的占位由后续入队/轮询清理，不需要后台清扫器。
_PRUNE = """
local clock = redis.call('TIME')
local now = clock[1] * 1000 + math.floor(clock[2] / 1000)
for _, member in ipairs(redis.call('ZRANGEBYSCORE', KEYS[2], '-inf', now)) do
    redis.call('ZREM', KEYS[1], member)
    redis.call('ZREM', KEYS[2], member)
end
"""
_ADMIT = (
    _PRUNE
    + """
local ahead = redis.call('ZCARD', KEYS[1])
if tonumber(ARGV[2]) >= 0 and ahead > tonumber(ARGV[2]) then
    return {0, ahead}
end
local sequence = redis.call('INCR', KEYS[3])
redis.call('ZADD', KEYS[1], sequence, ARGV[1])
redis.call('ZADD', KEYS[2], now + 15000, ARGV[1])
return {1, ahead}
"""
)
_PERMIT = (
    _PRUNE
    + """
if not redis.call('ZSCORE', KEYS[1], ARGV[1]) then return -1 end
redis.call('ZADD', KEYS[2], now + 15000, ARGV[1])
local head = redis.call('ZRANGE', KEYS[1], 0, 0)[1]
if head ~= ARGV[1] then return 250 end
local previous = tonumber(redis.call('GET', KEYS[3]) or '0')
local delay = previous + 1000 - now
if delay > 0 then return delay end
redis.call('ZREM', KEYS[1], ARGV[1])
redis.call('ZREM', KEYS[2], ARGV[1])
redis.call('SET', KEYS[3], now, 'PX', 2000)
return 0
"""
)
_REMOVE = """
redis.call('ZREM', KEYS[1], ARGV[1])
redis.call('ZREM', KEYS[2], ARGV[1])
return 0
"""


class S2Transport:
    def __init__(
        self,
        http: httpx.AsyncClient,
        redis: Redis,
        api_key: str,
        *,
        max_queued_ahead: int | None = None,
        namespace: str = "scholar:s2",
    ):
        self.http, self.redis, self._api_key = http, redis, api_key
        # None 用于离线调用：参与同一配额，但不应用在线拥堵准入阈值。
        self.max_queued_ahead = max_queued_ahead
        self._queue = f"{namespace}:queue"
        self._leases = f"{namespace}:leases"
        self._sequence = f"{namespace}:sequence"
        # 沿用旧发送许可键，正在运行的旧版离线任务仍共享 1 RPS。
        self._permit = f"{namespace}:send-permit"
        self.http.headers.pop("User-Agent", None)

    async def request(self, method: str, path: str, **kwargs):
        member = uuid4().hex
        try:
            accepted, ahead = await self.redis.eval(
                _ADMIT,
                3,
                self._queue,
                self._leases,
                self._sequence,
                member,
                -1 if self.max_queued_ahead is None else self.max_queued_ahead,
            )
            provider_queued_ahead.set(ahead)
            if not accepted:
                raise ProviderBusy(ahead, self.max_queued_ahead)
            while True:
                delay = await self.redis.eval(
                    _PERMIT,
                    3,
                    self._queue,
                    self._leases,
                    self._permit,
                    member,
                )
                if delay < 0:
                    raise ProviderError(
                        "S2 waiting lease expired before send permission"
                    )
                if delay == 0:
                    break
                await asyncio.sleep(delay / 1000)
        finally:
            # 取消、超时、Redis 故障均不得留下可绕过配额的发送路径。
            # Redis 暂时不可用时由租约回收，清理错误仍向调用者传播。
            await self.redis.eval(_REMOVE, 2, self._queue, self._leases, member)

        request = self.http.build_request(
            method,
            f"https://api.semanticscholar.org/{path}",
            headers={"x-api-key": self._api_key},
            **kwargs,
        )
        response = await self.http.send(request)
        response.raise_for_status()
        return response.json()
