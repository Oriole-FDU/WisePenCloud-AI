from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Dict, List

if TYPE_CHECKING:
    from chat.domain.entities.suspended_chat import SuspendedChat


class SuspendedChatRepository(ABC):
    @abstractmethod
    async def create(self, suspended_chat: SuspendedChat) -> SuspendedChat: ...

    @abstractmethod
    async def find_suspended_by_session(self, session_id: str, user_id: str) -> SuspendedChat | None: ...

    @abstractmethod
    async def find_suspended_by_sessions(self, session_ids: List[str], user_id: str) -> Dict[str, "SuspendedChat"]: ...

    @abstractmethod
    async def delete_by_id(self, suspended_chat_id: str) -> None: ...

    @abstractmethod
    async def delete_suspended_by_session(self, session_id: str, user_id: str) -> None: ...
