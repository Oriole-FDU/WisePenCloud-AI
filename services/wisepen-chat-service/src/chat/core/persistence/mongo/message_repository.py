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

    async def get_page_for_ui(
        self,
        session_id: str,
        page: int,
        size: int,
    ) -> Tuple[List[ChatMessage], int]:
        """
        以 user 消息为回合锚点的分页查询。
        page=1 = 最新的 size 个回合；page 递增 = 更早的回合。
        仅拉取当前页所需的原始消息，不会全量加载整个会话。
        """
        user_condition = [
            ChatMessage.session_id == session_id,
            ChatMessage.role == Role.USER,
        ]

        total_turns = await ChatMessage.find(*user_condition).count()
        if total_turns == 0:
            return [], 0

        skip = (page - 1) * size

        # 如果 page > 1，多拉 1 条更新的 user 消息作为时间窗口上界
        fetch_skip = max(0, skip - 1) if page > 1 else 0
        fetch_limit = size + (1 if page > 1 else 0)

        user_msgs = await ChatMessage.find(*user_condition) \
            .sort("-created_at") \
            .skip(fetch_skip) \
            .limit(fetch_limit) \
            .to_list()

        if not user_msgs:
            return [], total_turns

        if page > 1 and len(user_msgs) > 0:
            upper_bound_user = user_msgs[0]   # 前一页最末（更新）的 user 消息
            page_user_msgs = user_msgs[1:]    # 本页的 user 消息
        else:
            upper_bound_user = None
            page_user_msgs = user_msgs

        if not page_user_msgs:
            return [], total_turns

        # 本页最早的 user 消息 = 时间窗口下界
        oldest_user_time = min(m.created_at for m in page_user_msgs)

        msg_conditions = [
            ChatMessage.session_id == session_id,
            ChatMessage.created_at >= oldest_user_time,
            {"role": {"$in": [Role.USER.value, Role.ASSISTANT.value, Role.TOOL.value]}},
        ]

        # 上界：前一页最末 user 消息的 created_at（不含），确保不拉到更新页的数据
        if upper_bound_user is not None:
            msg_conditions.append(ChatMessage.created_at < upper_bound_user.created_at)

        page_msgs = await ChatMessage.find(*msg_conditions) \
            .sort("+created_at") \
            .to_list()

        return page_msgs, total_turns

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
