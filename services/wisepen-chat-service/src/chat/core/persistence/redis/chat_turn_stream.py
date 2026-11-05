import redis.asyncio as redis
import json

from chat.core.config.app_settings import settings


_RELEASE_IF_OWNER = """
if redis.call("GET", KEYS[1]) == ARGV[1] then
  return redis.call("DEL", KEYS[1])
else
  return 0
end
"""

# 续租和释放都必须比较 turn_id，避免误操作后续新 turn 的租约
_RENEW_IF_OWNER = """
if redis.call("GET", KEYS[1]) == ARGV[1] then
  return redis.call("EXPIRE", KEYS[1], ARGV[2])
else
  return 0
end
"""

_SUBMIT_SUBAGENT_COMMAND = """
local previous = redis.call("HGET", KEYS[2], ARGV[2])
if previous then
  local a = cjson.decode(previous)
  local b = cjson.decode(ARGV[3])
  if a.run_id ~= b.run_id or a.kind ~= b.kind or a.input ~= b.input then return "" end
  return previous
end
if ARGV[5] ~= "1" or redis.call("GET", KEYS[1]) ~= ARGV[1] then return "" end
if redis.call("HLEN", KEYS[2]) >= 256 then return "" end
redis.call("HSET", KEYS[2], ARGV[2], ARGV[3])
redis.call("EXPIRE", KEYS[2], ARGV[4])
return ARGV[3]
"""

_FINISH_SUBAGENT_COMMAND = """
local previous = redis.call("HGET", KEYS[1], ARGV[1])
if not previous then return 0 end
local command = cjson.decode(previous)
command.status = ARGV[2]
command.detail = ARGV[3]
redis.call("HSET", KEYS[1], ARGV[1], cjson.encode(command))
redis.call("EXPIRE", KEYS[1], ARGV[4])
return 1
"""


class RedisChatTurnStream:
    """保存 running turn 租约和可重放 SSE 事件流"""

    def __init__(self) -> None:
        self.redis = redis.from_url(settings.persistence.redis.url, decode_responses=True)
        self.active_turn_ttl = int(settings.chat_turn.active_ttl_seconds)
        self.stream_ttl = int(settings.chat_turn.stream_ttl_seconds)

    def _active_turn_key(self, user_id: str, session_id: str) -> str:
        return f"chat:session:{user_id}:{session_id}:active_turn"

    def _stream_key(self, turn_id: str) -> str:
        return f"chat:turn:{turn_id}:events"

    def _cancel_key(self, turn_id: str) -> str:
        return f"chat:turn:{turn_id}:cancel_requested"

    async def acquire_active_turn(self, user_id: str, session_id: str, turn_id: str) -> bool:
        # 原子写入，等价于 Redis 命令 SET chat:session:{user_id}:{session_id}:active_turn {turn_id} NX EX 1800
        # key 表示 这个用户的这个会话当前正在跑哪个 turn，用于防止一个会话有多个 trun 出现
        return bool(await self.redis.set(
            self._active_turn_key(user_id, session_id),
            turn_id,
            nx=True, # 只有 key 不存在时才写入，避免发生竞态问题
            ex=self.active_turn_ttl, # 运行期租约，防止后端进程崩溃、机器重启、runner 异常死亡时，session 永远被占住
        ))

    async def get_active_turn(self, user_id: str, session_id: str) -> str | None:
        # 获取当前对话的 turn
        return await self.redis.get(self._active_turn_key(user_id, session_id))

    async def renew_active_turn(self, user_id: str, session_id: str, turn_id: str) -> bool:
        # 续期当前对话的 turn
        result = await self.redis.eval(
            _RENEW_IF_OWNER,
            1,
            self._active_turn_key(user_id, session_id),
            turn_id,
            self.active_turn_ttl,
        )
        return bool(result)

    async def release_active_turn(self, user_id: str, session_id: str, turn_id: str) -> bool:
        # 释放当前对话的 turn，在 turn 自然结束或被中止后
        result = await self.redis.eval(
            _RELEASE_IF_OWNER,
            1,
            self._active_turn_key(user_id, session_id),
            turn_id,
        )
        return bool(result)

    async def request_turn_cancel(self, turn_id: str) -> None:
        # 取消请求只需要一个短期标记，runner 会在安全点读取并自行收尾。
        await self.redis.set(self._cancel_key(turn_id), "1", ex=self.stream_ttl)

    async def is_turn_cancel_requested(self, turn_id: str) -> bool:
        return await self.redis.get(self._cancel_key(turn_id)) == "1"

    def _subagent_commands_key(self, turn_id):
        return f"chat:turn:{turn_id}:subagent_commands"

    async def submit_subagent_command(self, user_id, session_id, turn_id, command, *, allow_new=True):
        result = await self.redis.eval(
            _SUBMIT_SUBAGENT_COMMAND, 2, self._active_turn_key(user_id, session_id),
            self._subagent_commands_key(turn_id), turn_id, command["command_id"],
            json.dumps({**command, "status": "accepted"}, ensure_ascii=False), self.stream_ttl,
            "1" if allow_new else "0",
        )
        return json.loads(result) if result else None

    async def pending_subagent_commands(self, turn_id):
        values = await self.redis.hvals(self._subagent_commands_key(turn_id))
        return [command for value in values if (command := json.loads(value))["status"] == "accepted"]

    async def finish_subagent_command(self, turn_id, command_id, status, detail=None):
        await self.redis.eval(
            _FINISH_SUBAGENT_COMMAND, 1, self._subagent_commands_key(turn_id),
            command_id, status, detail or "", self.stream_ttl,
        )

    async def append_frame(self, turn_id: str, frame: str, *, terminal: bool = False) -> str:
        # 将一个 SSE frame 写到当前 turn 的事件流里
        stream_key = self._stream_key(turn_id)
        event_id = await self.redis.xadd(
            stream_key,
            {
                "frame": frame, # 原样保存
                "terminal": "1" if terminal else "0",
            },
        )
        # 等价于 Redis：XADD chat:turn:turn_xxx:events * frame "data: {...}\n\n" terminal 0
        await self.redis.expire(stream_key, self.stream_ttl)
        return event_id

    async def iter_frames(self, turn_id: str):
        # 读取某个 turn 的 SSE frame，先 replay 已有事件，再等待新事件
        stream_key = self._stream_key(turn_id)
        last_id = "0-0"

        # 先完整重放已有事件，再阻塞等待新事件，支持页面断开后重新订阅
        entries = await self.redis.xrange(stream_key, min="-", max="+") # XRANGE - + 是读取这个 stream 里已有的所有事件
        for event_id, fields in entries:
            last_id = event_id
            yield fields.get("frame") or "", fields.get("terminal") == "1"
            if fields.get("terminal") == "1":
                return # turn 已经结束，直接 return，不再等待新事件

        # 如果已有事件重放完了，但还没结束，就进入循环
        while True:
            # 从 last_id 之后继续读，如果暂时没有新事件，最多等 4 秒，一次最多读 50 条
            result = await self.redis.xread({stream_key: last_id}, block=4000, count=50)
            if not result:
                continue # 继续循环

            for _, stream_entries in result:
                for event_id, fields in stream_entries:
                    last_id = event_id
                    yield fields.get("frame") or "", fields.get("terminal") == "1" # 更新 last_id，把新 frame yield 给浏览器
                    if fields.get("terminal") == "1":
                        return # 遇到终止事件，结束订阅
