"""S2 通道选择、Redis 发射节流及请求生命周期。"""

from __future__ import annotations

import asyncio
import hashlib
from contextvars import ContextVar
from uuid import uuid4

import httpx
from redis.asyncio import Redis

from scholar.domain.errors import ProviderBusy, ProviderError
from .batch_queue import S2BatchQueue

# S2 排队观测：记录当前请求在 FIFO 队列前等待的项数；不属于论文事实，不进入论文缓存。
s2_queued_ahead: ContextVar[int | None] = ContextVar("s2_queued_ahead", default=None)

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
local delay = previous + tonumber(ARGV[2]) - now
if delay > 0 then return delay end
-- 离线与在线兜底共用 key：在线待办优先，不抢占已经发出的请求。
if redis.call('LLEN', KEYS[5]) > 0 then return 50 end
if not redis.call('SET', KEYS[4], ARGV[1], 'NX', 'PX', 15000) then return 50 end
return 0
"""
)
_REMOVE = """
redis.call('ZREM', KEYS[1], ARGV[1])
redis.call('ZREM', KEYS[2], ARGV[1])
return 0
"""
_DIRECT_LAUNCHED = """
if redis.call('GET', KEYS[4]) ~= ARGV[1] then return 0 end
local clock = redis.call('TIME')
redis.call('SET', KEYS[3], clock[1] * 1000 + math.floor(clock[2] / 1000))
redis.call('ZREM', KEYS[1], ARGV[1])
redis.call('ZREM', KEYS[2], ARGV[1])
redis.call('DEL', KEYS[4])
return 1
"""
_RELEASE_GATE = """
if redis.call('GET', KEYS[1]) == ARGV[1] then redis.call('DEL', KEYS[1]) end
return 0
"""


class S2Transport:
    def __init__(
        self,
        http: httpx.AsyncClient,
        redis: Redis,
        api_key: str,
        *,
        max_queued_ahead: int | None = 3,
        namespace: str = "scholar:s2",
        nokey_interval: float = 1.5,
        key_interval: float = 1.5,
        batch_max_ids: int = 300,
        request_timeout: float = 25,
        offline: bool = False,
    ):
        self.http, self.redis, self._api_key = http, redis, api_key
        # 离线使用独立双通道待办；KEY 兜底仍与在线共享同一凭证的发射锚点。
        self.max_queued_ahead = max_queued_ahead
        self.namespace = namespace
        self.key_interval, self.request_timeout, self.offline = key_interval, request_timeout, offline
        self.http.headers.pop("User-Agent", None)
        self.batches = S2BatchQueue(
            http, redis, api_key, namespace=namespace, nokey_interval=nokey_interval,
            key_interval=key_interval, batch_max_ids=batch_max_ids,
            max_queued_ahead=None if offline else max_queued_ahead,
            request_timeout=request_timeout, offline=offline,
        )

    async def start(self):
        await self.batches.start()

    async def aclose(self):
        await self.batches.aclose()

    async def __aenter__(self):
        await self.start()
        return self

    async def __aexit__(self, *_args):
        await self.aclose()

    async def request(self, method: str, path: str, *, api_key: str | None = None, **kwargs):
        # 预算覆盖入队、合流、降级与 HTTP；离线使用独立的 ETL 请求预算。
        async with asyncio.timeout(self.request_timeout):
            effective_key = api_key or self._api_key
            platform = effective_key == self._api_key
            if platform and method == "POST" and path == "graph/v1/paper/batch":
                try:
                    ahead, future, job = await self.batches.submit(kwargs["json"]["ids"], kwargs["params"])
                except ProviderBusy as error:
                    s2_queued_ahead.set(error.queued_ahead)
                    raise
                s2_queued_ahead.set(ahead)
                try:
                    return await asyncio.shield(future)
                finally:
                    await self.batches.cancel(job)
            # 平台沿用原队列以继续与离线共享；用户配额按 key 隔离且不设准入阈值。
            namespace = self.namespace if platform else f"{self.namespace}:byok:{hashlib.sha256(effective_key.encode()).hexdigest()}"
            queue, leases, sequence, permit = [f"{namespace}:{suffix}" for suffix in
                                               ("queue", "leases", "sequence", "send-permit")]
            max_ahead = self.max_queued_ahead if platform else None
            if self.offline:
                max_ahead = None
            gate = f"{self.namespace}:key:launching" if platform else f"{namespace}:launching"
            priority = f"{self.namespace}:online:key:queue" if platform else f"{namespace}:priority"
            request = self.http.build_request(
                method, f"https://api.semanticscholar.org/{path}",
                headers={"x-api-key": effective_key}, **kwargs,
            )
            member = uuid4().hex
            started = asyncio.Event()
            task = None
            recorded = False
            try:
                accepted, ahead = await self.redis.eval(
                    _ADMIT,
                    3,
                    queue,
                    leases,
                    sequence,
                    member,
                    -1 if max_ahead is None else max_ahead,
                )
                s2_queued_ahead.set(ahead)
                if not accepted:
                    raise ProviderBusy(ahead, max_ahead)
                while True:
                    delay = await self.redis.eval(
                        _PERMIT,
                        5,
                        queue,
                        leases,
                        permit,
                        gate,
                        priority,
                        member,
                        round(self.key_interval * 1000),
                    )
                    if delay < 0:
                        raise ProviderError(
                            "S2 waiting lease expired before send permission"
                        )
                    if delay == 0:
                        break
                    await asyncio.sleep(min(delay, 1000) / 1000)
                task = asyncio.create_task(self._send_direct(request, started))
                await started.wait()
                recorded = bool(await self.redis.eval(
                    _DIRECT_LAUNCHED, 4, queue, leases, permit, gate, member,
                ))
                if not recorded:
                    raise ProviderError("S2 launch lease expired before dispatch acknowledgement")
                response = await task
                response.raise_for_status()
                return response.json()
            finally:
                # 取消、超时、Redis 故障均不得留下可绕过配额的发送路径；
                # 清理失败不吞掉，交由调用者感知。
                if task is not None:
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
                if started.is_set() and not recorded:
                    await self.redis.eval(_DIRECT_LAUNCHED, 4, queue, leases, permit, gate, member)
                await self.redis.eval(_REMOVE, 2, queue, leases, member)
                await self.redis.eval(_RELEASE_GATE, 1, gate, member)

    async def _send_direct(self, request, started):
        started.set()
        return await self.http.send(request)


