import asyncio
import uuid
from collections.abc import AsyncIterator, Callable

from fastapi import BackgroundTasks

from chat.api.vercel_formats import message_finish, message_start, stream_done, error as sse_error
from chat.core.persistence.redis.chat_turn_stream import RedisChatTurnStream
from chat.domain.error_codes import ChatErrorCode
from common.core.exceptions import ServiceException
from common.logger import error, info


class ChatTurnStreamManager:
    """管理 running turn 租约、后台 runner 和 SSE 事件订阅。"""

    def __init__(self, *, stream_repo: RedisChatTurnStream) -> None:
        self._stream_repo = stream_repo
        self._tasks: dict[str, asyncio.Task] = {}

    async def start_turn(
        self,
        *,
        user_id: str,
        session_id: str,
        build_chat_gen: Callable[[BackgroundTasks, str], AsyncIterator[str]],
    ) -> str:
        turn_id = f"turn_{uuid.uuid4().hex}" # 对话轮次 ID 同时作为运行租约的值和事件流标识
        acquired = await self._stream_repo.acquire_active_turn(user_id, session_id, turn_id) # 申请租约
        if not acquired: # 无法申请租约，说明有在运行的 turn，报错
            raise ServiceException(ChatErrorCode.CHAT_TURN_IN_PROGRESS)

        # 放到当前事件循环里后台执行，调用者不会等待它完成，立刻返回 turn_id
        task = asyncio.create_task(
            self._run_turn(
                user_id=user_id,
                session_id=session_id,
                turn_id=turn_id,
                build_chat_gen=build_chat_gen,
            ),
            name=f"chat-turn-runner:{turn_id}",
        )
        self._tasks[turn_id] = task # 避免后台任务变成无人持有的悬空任务
        task.add_done_callback(lambda finished_task, key=turn_id: self._tasks.pop(key, None)) # 任务结束后自动移除，避免内存泄漏
        return turn_id

    async def cancel_turn(self, *, user_id: str, session_id: str) -> str:
        turn_id = await self.active_turn_id(user_id=user_id, session_id=session_id)
        if turn_id is None:
            raise ServiceException(ChatErrorCode.CHAT_ACTIVE_TURN_NOT_FOUND)

        await self._stream_repo.request_turn_cancel(turn_id)
        task = self._tasks.get(turn_id)

        if task is None: return turn_id

        try: # 30s秒内仍不退出则强杀
            await asyncio.wait_for(asyncio.shield(task), timeout=30)
        except asyncio.TimeoutError:
            task.cancel()
        return turn_id

    async def is_turn_cancel_requested(self, turn_id: str) -> bool:
        return await self._stream_repo.is_turn_cancel_requested(turn_id)

    async def active_turn_id(self, *, user_id: str, session_id: str) -> str | None:
        return await self._stream_repo.get_active_turn(user_id, session_id)

    async def subscribe_turn(self, turn_id: str) -> AsyncIterator[str]:
        # 订阅某个具体 turn_id 的 Redis Stream，并把里面的 SSE frame 原样 yield 给 HTTP 响应
        try:
            async for frame, _terminal in self._stream_repo.iter_frames(turn_id):
                yield frame
        except asyncio.CancelledError:
            info("chat turn stream subscriber cancelled.", turn_id=turn_id)
            return

    async def _run_turn(
        self,
        *,
        user_id: str,
        session_id: str,
        turn_id: str,
        build_chat_gen: Callable[[BackgroundTasks, str], AsyncIterator[str]],
    ) -> None:
        # 创建一个后台执行环境
        background_tasks = BackgroundTasks()

        try:
            # 后台执行器统一负责消息生命周期帧，页面连接只转发。
            await self._stream_repo.append_frame(turn_id, message_start(f"msg_{uuid.uuid4().hex}"))

            async for frame in build_chat_gen(background_tasks, turn_id):
                # 业务输出帧写入前检查租约，避免旧 runner 在会话所有权失效后继续写内容
                renewed = await self._stream_repo.renew_active_turn(user_id, session_id, turn_id) # 续期
                if not renewed: # 无法续期，中止任务
                    await self._stream_repo.append_frame(turn_id, sse_error(error_text="当前对话运行租约已失效"))
                    await self._stream_repo.append_frame(turn_id, stream_done(), terminal=True)
                    return
                await self._stream_repo.append_frame(turn_id, frame)

            # 在执行后台任务前释放 active_turn
            await self._stream_repo.release_active_turn(user_id, session_id, turn_id)

            await self._stream_repo.append_frame(turn_id, message_finish())
            await self._stream_repo.append_frame(turn_id, stream_done(), terminal=True)
        except Exception as exc:
            error("chat turn runner failed.", turn_id=turn_id, session_id=session_id, exc=exc)
            await self._stream_repo.append_frame(turn_id, sse_error(error_text=str(exc)))
            await self._stream_repo.append_frame(turn_id, stream_done(), terminal=True)
        finally:
            self._tasks.pop(turn_id, None)
            try:
                await background_tasks()
            except Exception as exc:
                error("chat turn background tasks failed.", turn_id=turn_id, session_id=session_id, exc=exc)
