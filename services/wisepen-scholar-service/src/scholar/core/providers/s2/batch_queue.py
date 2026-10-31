"""S2 平台请求的 Redis 跨进程合流、独立发射与按调用回执分发。"""

from __future__ import annotations

import asyncio
import json
import math
from dataclasses import dataclass
from enum import StrEnum
from uuid import uuid4

import httpx
from common.logger import warning
from redis.asyncio import Redis
from redis.exceptions import RedisError

from scholar.domain.errors import ProviderBusy, ProviderError, provider_error_message


# 批次保存原始位置映射；取消只删除 job，不会破坏同批其他调用。
_CLEAN = """
local function clean(batch, prefix)
    local used = {}
    for job, positions in pairs(batch.jobs) do
        if redis.call('EXISTS', prefix .. ':job:' .. job) == 0 then
            batch.jobs[job] = nil
        else
            for _, position in ipairs(positions) do used[position[2]] = true end
        end
    end
    local ids, remap = {}, {}
    for index, id in ipairs(batch.ids) do
        if used[index] then
            table.insert(ids, id)
            remap[index] = #ids
        end
    end
    for _, positions in pairs(batch.jobs) do
        for _, position in ipairs(positions) do position[2] = remap[position[2]] end
    end
    batch.ids = ids
    return #ids > 0
end
local clock = redis.call('TIME')
local now = clock[1] * 1000 + math.floor(clock[2] / 1000)
"""

_SUBMIT = _CLEAN + """
local prefix, job, reply = ARGV[1], ARGV[2], ARGV[3]
local ids, params = cjson.decode(ARGV[4]), ARGV[5]
local capacity, limit, ttl = tonumber(ARGV[6]), tonumber(ARGV[7]), tonumber(ARGV[8])
local batches = {}
for _, id in ipairs(redis.call('LRANGE', KEYS[1], 0, -1)) do
    local raw = redis.call('GET', prefix .. ':batch:' .. id)
    if raw then
        local batch = cjson.decode(raw)
        if clean(batch, prefix) then table.insert(batches, batch) end
    end
end
local ahead = 0
-- 先在脚本内试装，准入失败不留下 job 或半份批次。
for position, id in ipairs(ids) do
    local target, index
    for number, batch in ipairs(batches) do
        if not batch.claimed and batch.params == params then
            for candidate, existing in ipairs(batch.ids) do
                if existing == id then target, index = number, candidate; break end
            end
        end
        if target then break end
    end
    if not target then
        local tail = batches[#batches]
        if not tail or tail.claimed or tail.params ~= params or #tail.ids == capacity then
            if limit >= 0 and #batches > limit then return {0, #batches} end
            tail = {id=job .. '-' .. position, ids={}, jobs={}, params=params}
            table.insert(batches, tail)
        end
        table.insert(tail.ids, id)
        target, index = #batches, #tail.ids
    end
    ahead = math.max(ahead, target - 1)
    local batch = batches[target]
    if not batch.jobs[job] then batch.jobs[job] = {} end
    table.insert(batch.jobs[job], {position, index})
end
redis.call('SET', prefix .. ':job:' .. job, reply, 'PX', ttl)
redis.call('DEL', KEYS[1])
for _, batch in ipairs(batches) do
    redis.call('SET', prefix .. ':batch:' .. batch.id, cjson.encode(batch), 'PX', ttl)
    redis.call('RPUSH', KEYS[1], batch.id)
end
redis.call('SET', KEYS[2], now + tonumber(ARGV[9]), 'NX')
return {1, ahead}
"""

_CLAIM = _CLEAN + """
local prefix, token = ARGV[1], ARGV[2]
local owner = redis.call('GET', KEYS[1])
if owner and owner ~= token then return {0, 100} end
redis.call('SET', KEYS[1], token, 'PX', 15000)
while true do
    local id = redis.call('LINDEX', KEYS[2], 0)
    if not id then return {0, 50} end
    local raw = redis.call('GET', prefix .. ':batch:' .. id)
    if raw then
        local batch = cjson.decode(raw)
        if batch.claimed then
            -- 前消费者已领取但未完成记账，结果不确定；租约结束后丢弃，禁止自动重放。
            if redis.call('EXISTS', KEYS[4]) == 1 then return {0, 50} end
        elseif clean(batch, prefix) then
            local previous = redis.call('GET', KEYS[3])
            local due = previous and tonumber(previous) + tonumber(ARGV[3])
                or tonumber(redis.call('GET', KEYS[5]) or '0')
            if due > now then return {0, math.min(due - now, 1000)} end
            -- 离线有自己的双通道待办，但同 key 的额度优先留给在线兜底。
            if ARGV[5] == '1' and redis.call('LLEN', KEYS[6]) > 0 then return {0, 50} end
            if not redis.call('SET', KEYS[4], token, 'NX', 'PX', 15000) then
                return {0, 50}
            end
            -- 领取后封批，发射记账前到达的新请求不能再装入本批。
            batch.claimed = true
            redis.call('SET', prefix .. ':batch:' .. id, cjson.encode(batch), 'PX', ARGV[4])
            return {1, cjson.encode(batch)}
        end
    end
    redis.call('LPOP', KEYS[2])
    redis.call('DEL', prefix .. ':batch:' .. id)
end
"""

# 锚点记在 HTTP 协程已经开始之后，保守覆盖 Redis 往返延迟；下一批不追赶。
_LAUNCHED = """
if redis.call('GET', KEYS[1]) ~= ARGV[1] then return 0 end
local clock = redis.call('TIME')
redis.call('SET', KEYS[2], clock[1] * 1000 + math.floor(clock[2] / 1000))
redis.call('DEL', KEYS[1])
if redis.call('LINDEX', KEYS[3], 0) == ARGV[2] then redis.call('LPOP', KEYS[3]) end
redis.call('DEL', KEYS[4])
return 1
"""

_RELEASE = """
if redis.call('GET', KEYS[1]) == ARGV[1] then redis.call('DEL', KEYS[1]) end
return 0
"""

_REPLY = """
for _, item in ipairs(cjson.decode(ARGV[2])) do
    local stream = redis.call('GET', ARGV[1] .. ':job:' .. item.job)
    if stream then
        redis.call('XADD', stream, '*', 'job', item.job, 'data', item.data)
        redis.call('PEXPIRE', stream, ARGV[3])
    end
end
return 0
"""


class ReplyKind(StrEnum):
    DATA = "data"
    HTTP_ERROR = "http_error"
    TIMEOUT = "timeout"
    ERROR = "error"


@dataclass
class PendingCall:
    """仅存于提交进程，跨批片段收齐后才完成原调用。"""

    future: asyncio.Future
    values: list
    remaining: set[int]


class S2BatchQueue:
    """平台双通道协调器；每个实例提交任务、竞争消费者并接收自己的回执。"""

    def __init__(
        self,
        http: httpx.AsyncClient,
        redis: Redis,
        api_key: str,
        *,
        namespace: str,
        nokey_interval: float,
        key_interval: float,
        batch_max_ids: int,
        max_queued_ahead: int | None,
        request_timeout: float,
        offline: bool = False,
    ):
        self.http, self.redis, self._api_key = http, redis, api_key
        self.namespace = namespace
        self.offline = offline
        self.prefix = f"{namespace}:{'offline' if offline else 'online'}"
        self.intervals = {"nokey": nokey_interval, "key": key_interval}
        self.batch_max_ids = batch_max_ids
        self.max_queued_ahead = max_queued_ahead
        self.request_timeout = request_timeout
        # 租约 TTL 覆盖请求超时 + Redis 往返余量。
        self.ttl = math.ceil((request_timeout + 15) * 1000)
        self.token = uuid4().hex
        self.reply_stream = f"{self.prefix}:reply:{self.token}"
        self.pending: dict[str, PendingCall] = {}
        self._tasks: list[asyncio.Task] = []
        self._inflight: set[asyncio.Task] = set()
        self._failure: BaseException | None = None
        self._closed = False

    async def start(self):
        if not self._tasks:
            self._tasks = [asyncio.create_task(self._receive())]
            self._tasks.extend(
                asyncio.create_task(self._consume(channel))
                for channel in self.intervals
            )
            for task in self._tasks:
                task.add_done_callback(self._task_done)

    def _task_done(self, task):
        if task.cancelled():
            return
        error = task.exception()
        if error is not None:
            # 后台任务失败必须到达等待者，不能留下永远不完成的 Future。
            self._failure = error
            for call in self.pending.values():
                call.future.set_exception(error)
            self.pending.clear()
            asyncio.get_running_loop().call_exception_handler(
                {"message": "S2 batch worker failed", "exception": error}
            )

    async def submit(
        self,
        ids: list[str],
        params: dict,
    ) -> tuple[int, asyncio.Future, str]:
        if self._closed:
            raise ProviderError("S2 batch coordinator is closed")
        if self._failure is not None:
            raise self._failure
        await self.start()

        job = uuid4().hex
        future = asyncio.get_running_loop().create_future()
        self.pending[job] = PendingCall(
            future, [None] * len(ids), set(range(len(ids)))
        )
        try:
            accepted, ahead = await self.redis.eval(
                _SUBMIT,
                2,
                f"{self.prefix}:nokey:queue",
                f"{self.prefix}:first-due",
                self.prefix,
                job,
                self.reply_stream,
                json.dumps(ids),
                json.dumps(params, sort_keys=True),
                self.batch_max_ids,
                -1 if self.max_queued_ahead is None else self.max_queued_ahead,
                self.ttl,
                round(self.intervals["nokey"] * 1000),
            )
            if not accepted:
                raise ProviderBusy(ahead, self.max_queued_ahead)
            return ahead, future, job
        except (asyncio.CancelledError, RedisError, ProviderBusy):
            # 提交与取消存在竞争，按 job 精确撤销；异常继续原样传播。
            await self.cancel(job)
            raise

    async def cancel(self, job):
        call = self.pending.pop(job, None)
        if call is not None:
            call.future.cancel()
        await self.redis.delete(f"{self.prefix}:job:{job}")

    async def _consume(self, channel):
        root = f"{self.prefix}:{channel}"
        owner, queue = f"{root}:owner", f"{root}:queue"
        anchor = (
            f"{self.namespace}:send-permit"
            if channel == "key"
            else f"{root}:last-launch"
        )
        gate = (
            f"{self.namespace}:key:launching"
            if channel == "key"
            else f"{root}:launching"
        )
        try:
            while True:
                claimed, value = await self.redis.eval(
                    _CLAIM,
                    6,
                    owner,
                    queue,
                    anchor,
                    gate,
                    (
                        f"{self.prefix}:first-due"
                        if channel == "nokey"
                        else f"{self.prefix}:key:first-due"
                    ),
                    f"{self.namespace}:online:key:queue",
                    self.prefix,
                    self.token,
                    round(self.intervals[channel] * 1000),
                    self.ttl,
                    int(self.offline and channel == "key"),
                )
                if not claimed:
                    await asyncio.sleep(value / 1000)
                    continue

                batch = json.loads(value)
                started = asyncio.Event()
                acknowledged = asyncio.Event()
                task = asyncio.create_task(
                    self._send(batch, channel, started, acknowledged)
                )
                self._inflight.add(task)
                task.add_done_callback(self._inflight.discard)
                task.add_done_callback(self._task_done)
                try:
                    await started.wait()
                finally:
                    # 即使此刻取消，已经启动的请求也必须先记账，再允许下一次发射。
                    try:
                        recorded = await self.redis.eval(
                            _LAUNCHED,
                            4,
                            gate,
                            anchor,
                            queue,
                            f"{self.prefix}:batch:{batch['id']}",
                            self.token,
                            batch["id"],
                        )
                        if not recorded:
                            raise ProviderError(
                                "S2 launch lease expired before dispatch acknowledgement"
                            )
                    except (RedisError, ProviderError):
                        task.cancel()
                        await asyncio.gather(task, return_exceptions=True)
                        raise
                    acknowledged.set()
        finally:
            await self.redis.eval(_RELEASE, 1, owner, self.token)

    async def _send(self, batch, channel, started, acknowledged):
        request = self.http.build_request(
            "POST",
            "https://api.semanticscholar.org/graph/v1/paper/batch",
            params=json.loads(batch["params"]),
            json={"ids": batch["ids"]},
        )
        request.headers.pop("x-api-key", None)
        if channel == "key":
            request.headers["x-api-key"] = self._api_key

        # set 与 send 之间没有 await；消费者被唤醒时 HTTP 协程已经进入发送路径。
        started.set()
        try:
            response = await self.http.send(request)
        except httpx.RequestError as error:
            await acknowledged.wait()
            await self._complete(
                batch,
                {
                    "kind": ReplyKind.TIMEOUT if isinstance(error, httpx.TimeoutException) else ReplyKind.ERROR,
                    "message": provider_error_message(error),
                },
            )
            return
        await acknowledged.wait()

        if response.status_code == 429 and channel == "nokey":
            # 只投递到独立节流的 KEY 队列；外层 lookup 整体失败后才决定退避重试。
            batch.pop("claimed")
            async with self.redis.pipeline(transaction=True) as pipe:
                pipe.set(
                    f"{self.prefix}:batch:{batch['id']}",
                    json.dumps(batch),
                    px=self.ttl,
                )
                pipe.rpush(f"{self.prefix}:key:queue", batch["id"])
                _, key_queue_size = await pipe.execute()
            warning(
                "S2 NOKEY rate limited; queued KEY fallback.",
                scope="offline" if self.offline else "online",
                batch_id=batch["id"],
                id_count=len(batch["ids"]),
                status_code=429,
                from_channel="NOKEY",
                to_channel="KEY",
                key_queued_ahead=key_queue_size - 1,
                key_interval_seconds=self.intervals["key"],
            )
            return
        if self.offline and response.status_code == 400 and "no valid paper ids" in response.text.lower():
            # 离线原有的“全批未收录”语义必须在子批边界处理，不能抹掉其他子批的成功结果。
            await self._complete(batch, {"kind": ReplyKind.DATA}, [None] * len(batch["ids"]))
            return
        if response.status_code != 200:
            await self._complete(
                batch,
                {
                    "kind": ReplyKind.HTTP_ERROR,
                    "status": response.status_code,
                    "body": response.text,
                },
            )
            return
        try:
            values = response.json()
            if not isinstance(values, list) or len(values) != len(batch["ids"]):
                raise ValueError(
                    "S2 batch response does not match the submitted ID count"
                )
        except ValueError as error:
            await self._complete(
                batch, {"kind": ReplyKind.ERROR, "message": str(error)}
            )
            return
        await self._complete(batch, {"kind": ReplyKind.DATA}, values)

    async def _complete(self, batch, outcome, values=None):
        replies = []
        for job, mapping in batch["jobs"].items():
            payload = dict(outcome)
            if outcome["kind"] == ReplyKind.DATA:
                payload["positions"] = [position - 1 for position, _ in mapping]
                payload["values"] = [values[index - 1] for _, index in mapping]
            replies.append({"job": job, "data": json.dumps(payload)})
        await self.redis.eval(_REPLY, 0, self.prefix, json.dumps(replies), self.ttl)

    async def _receive(self):
        cursor = "0-0"
        while True:
            messages = await self.redis.xread(
                {self.reply_stream: cursor}, block=1000, count=100
            )
            for _, entries in messages:
                for cursor, fields in entries:
                    job = fields[b"job"].decode()
                    call = self.pending.get(job)
                    if call is not None:
                        payload = json.loads(fields[b"data"])
                        if payload["kind"] == ReplyKind.DATA:
                            for position, value in zip(
                                payload["positions"],
                                payload["values"],
                                strict=True,
                            ):
                                call.values[position] = value
                            call.remaining.difference_update(payload["positions"])
                            if not call.remaining:
                                self.pending.pop(job)
                                call.future.set_result(call.values)
                        else:
                            self.pending.pop(job)
                            if payload["kind"] == ReplyKind.HTTP_ERROR:
                                request = httpx.Request(
                                    "POST",
                                    "https://api.semanticscholar.org/graph/v1/paper/batch",
                                )
                                response = httpx.Response(
                                    payload["status"],
                                    text=payload["body"],
                                    request=request,
                                )
                                try:
                                    response.raise_for_status()
                                except httpx.HTTPStatusError as error:
                                    call.future.set_exception(error)
                                else:
                                    call.future.set_exception(
                                        httpx.HTTPStatusError(
                                            f"Unexpected S2 status {response.status_code}",
                                            request=request,
                                            response=response,
                                        )
                                    )
                            elif payload["kind"] == ReplyKind.TIMEOUT:
                                call.future.set_exception(TimeoutError(payload["message"]))
                            else:
                                call.future.set_exception(
                                    ProviderError(payload["message"])
                                )
                    await self.redis.xdel(self.reply_stream, cursor)

    async def aclose(self):
        self._closed = True
        jobs = list(self.pending)
        for call in self.pending.values():
            call.future.set_exception(
                ProviderError("S2 batch coordinator stopped")
            )
        self.pending.clear()

        tasks = [*self._tasks, *self._inflight]
        for task in tasks:
            task.cancel()
        results = await asyncio.gather(*tasks, return_exceptions=True)
        await self.redis.delete(
            self.reply_stream,
            *(f"{self.prefix}:job:{job}" for job in jobs),
        )
        for result in results:
            if isinstance(result, BaseException) and not isinstance(
                result, asyncio.CancelledError
            ):
                raise result
