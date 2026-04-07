from abc import ABC, abstractmethod
from typing import List, Tuple
from datetime import datetime
from chat.domain.entities import ChatSession


class SessionRepository(ABC):
    """冷数据：会话仓储接口 (MongoDB)"""

    @abstractmethod
    async def create(self, session: ChatSession) -> ChatSession: pass

    @abstractmethod
    async def get_by_id(self, session_id: str) -> ChatSession:
        pass

    @abstractmethod
    async def get_by_id_and_user(self, session_id: str, user_id: str) -> ChatSession: pass

    @abstractmethod
    async def get_by_user(self, user_id: str, page: int, size: int) -> Tuple[List[ChatSession], int]: pass

    @abstractmethod
    async def update_summary(self, session_id: str, current_summary: str, summary_updated_at: datetime) -> None: pass

    @abstractmethod
    async def delete(self, session_id: str, user_id: str) -> None: pass

    @abstractmethod
    async def rename(self, session_id: str, user_id: str, new_title: str) -> ChatSession: pass

    @abstractmethod
    async def set_pin(self, session_id: str, user_id: str, is_pinned: bool) -> ChatSession: pass

