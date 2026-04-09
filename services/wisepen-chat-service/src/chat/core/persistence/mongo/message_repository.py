from typing import List, Optional, Tuple
from datetime import datetime
from beanie.odm.operators.find.evaluation import Text

from chat.domain.repositories import MessageRepository
from chat.domain.entities import ChatMessage, Role




class MongoMessageRepository(MessageRepository):

    async def save_many(self, messages: List[ChatMessage]) -> None:
        if messages:
            await ChatMessage.insert_many(messages)

    async def get_by_session(self, session_id: str, after: datetime = None, limit: int = 50) -> List[ChatMessage]:
        conditions = [ChatMessage.session_id == session_id]
        if after:
            conditions.append(ChatMessage.created_at > after)
        return await ChatMessage.find(*conditions).sort("+created_at").limit(limit).to_list()

    async def get_after_time(self, session_id: str, after: datetime, limit: int) -> List[ChatMessage]:
        return await ChatMessage.find(
            ChatMessage.session_id == session_id,
            ChatMessage.created_at > after,
        ).sort("+created_at").limit(limit).to_list()

    async def get_page_by_session(
        self,
        session_id: str,
        page: int,
        size: int,
    ) -> Tuple[List[ChatMessage], int]:
        """
        分页拉取会话消息，过滤掉 system / tool 结果消息，仅保留 user 和 assistant（含 tool_calls）
        """
        conditions = [
            ChatMessage.session_id == session_id,
            {"role": {"$in": [r.value for r in [Role.USER, Role.ASSISTANT]]}},
        ]
        query = ChatMessage.find(*conditions)
        total = await query.count()
        items = await query.sort("+created_at").skip((page - 1) * size).limit(size).to_list()
        return items, total

    async def full_text_search(
        self,
        keyword: str,
        session_id: Optional[str] = None,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        limit: int = 10,
    ) -> List[ChatMessage]:
        """
        利用 MongoDB $text 全文索引进行关键词检索。
        支持 session_id 过滤和创建时间范围收窄，结果按相关度排序。
        """
        conditions = []
        conditions.append(Text(keyword))
        if session_id:
            conditions.append(ChatMessage.session_id == session_id)
        if start_time:
            conditions.append(ChatMessage.created_at >= start_time)
        if end_time:
            conditions.append(ChatMessage.created_at <= end_time)

        return await ChatMessage.find(*conditions).sort("+created_at").limit(limit).to_list()
