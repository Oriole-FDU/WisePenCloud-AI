from typing import List

from chat.core.persistence.redis.chat_turn_stream import RedisChatTurnStream
from chat.core.persistence.redis.frontend_tool_response import RedisFrontendToolResponseRepository
from chat.domain.entities import ChatSession, SessionStatus
from chat.domain.repositories import SuspendedChatRepository


class SessionStatusResolver:
    """
    派生会话运行时状态（generating / awaiting_user_response / suspended）
    并将持久状态（normal / completed_unread / error_unread）归一到前端可见状态

    规则：
    - 有 active turn 且 frontend_tool_response 等待标记存在 -> awaiting_user_response
    - 有 active turn -> generating（包括正在恢复的挂起会话）
    - 有 SuspendedChat（前端响应超时后） -> suspended
    - 否则 -> 会话持久状态（normal / completed_unread / error_unread）
    """

    def __init__(
        self,
        stream_repo: RedisChatTurnStream,
        frontend_response_repo: RedisFrontendToolResponseRepository,
        suspended_chat_repo: SuspendedChatRepository,
    ):
        self._stream_repo = stream_repo
        self._frontend_response_repo = frontend_response_repo
        self._suspended_chat_repo = suspended_chat_repo

    async def resolve(self, user_id: str, session: ChatSession) -> SessionStatus:
        statuses = await self.resolve_batch(user_id, [session])
        return statuses.get(str(session.id), SessionStatus.NORMAL)

    async def resolve_batch(self, user_id: str, sessions: List[ChatSession]) -> dict[str, SessionStatus]:
        if not sessions:
            return {}
        sessions_by_id = {str(s.id): s for s in sessions}
        session_ids = list(sessions_by_id.keys())

        active_turns = await self._stream_repo.get_active_turns_batch(user_id, session_ids)
        waits = await self._frontend_response_repo.get_waits(session_ids)
        suspended = await self._suspended_chat_repo.find_suspended_by_sessions(session_ids, user_id)

        result: dict[str, SessionStatus] = {}
        for sid in session_ids:
            result[sid] = self._resolve_one(
                turn_id=active_turns.get(sid),
                wait=waits.get(sid),
                suspended_chat=suspended.get(sid),
                persisted_status=sessions_by_id[sid].status,
            )
        return result

    @staticmethod
    def _resolve_one(
        *,
        turn_id: str | None,
        wait: dict | None,
        suspended_chat,
        persisted_status: SessionStatus,
    ) -> SessionStatus:
        if turn_id is not None:
            if wait is not None:
                return SessionStatus.AWAITING_USER_RESPONSE
            return SessionStatus.GENERATING
        if suspended_chat is not None:
            return SessionStatus.SUSPENDED
        return persisted_status
