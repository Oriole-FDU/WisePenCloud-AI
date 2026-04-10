from typing import List, Tuple
from datetime import datetime, timezone

from beanie import PydanticObjectId

from common.core.exceptions import ServiceException
from chat.domain.repositories import SessionRepository
from chat.domain.entities import ChatSession
from chat.domain.error_codes import ChatErrorCode


class MongoSessionRepository(SessionRepository):

    async def create(self, session: ChatSession) -> ChatSession:
        await session.insert()
        return session

    async def get_by_id(self, session_id: str) -> ChatSession:
        session = await ChatSession.get(PydanticObjectId(session_id))
        if session is None:
            raise ServiceException(ChatErrorCode.SESSION_NOT_FOUND)
        return session

    async def get_by_id_and_user(self, session_id: str, user_id: str) -> ChatSession:
        """联合查询，查不到（不存在或不属于该用户）统一抛 SESSION_NOT_FOUND，防止枚举他人 session_id。"""
        session = await ChatSession.find_one(
            ChatSession.id == PydanticObjectId(session_id),
            ChatSession.user_id == user_id,
        )
        if session is None:
            raise ServiceException(ChatErrorCode.SESSION_NOT_FOUND)
        return session

    async def get_by_user(self, user_id: str, page: int, size: int) -> Tuple[List[ChatSession], int]:
        """分页拉取用户会话列表，按 updated_at 降序，返回 (当页列表, 总数)"""
        query = ChatSession.find(ChatSession.user_id == user_id)
        total = await query.count()
        items = await query.sort(
            "-is_pinned",    
            "-pinned_at",      
            "-updated_at"      
        ).skip((page - 1) * size).limit(size).to_list()
        return items, total

    async def update_summary(self, session_id: str, current_summary: str, summary_updated_at: datetime) -> None:
        session = await ChatSession.get(PydanticObjectId(session_id))
        if session:
            session.current_summary = current_summary
            session.summary_updated_at = summary_updated_at
            await session.save()

    async def delete(self, session_id: str, user_id: str) -> None:
        session = await self.get_by_id_and_user(session_id, user_id)
        await session.delete()

    async def rename(self, session_id: str, user_id: str, new_title: str) -> ChatSession:
        session = await self.get_by_id_and_user(session_id, user_id)
        session.title = new_title
        session.updated_at = datetime.now(timezone.utc)
        await session.save()
        return session

    async def pin(self, session_id: str, user_id: str, is_pinned: bool) -> ChatSession:
        session = await self.get_by_id_and_user(session_id, user_id)
        session.is_pinned = is_pinned
        session.pinned_at = datetime.now(timezone.utc) if is_pinned else None   # 取消置顶时，置顶时间设为 None
        session.updated_at = datetime.now(timezone.utc)
        await session.save()
        return session


