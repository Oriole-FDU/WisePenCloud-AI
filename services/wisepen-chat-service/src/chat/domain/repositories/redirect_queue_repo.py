from abc import ABC, abstractmethod
from typing import List


class RedirectQueueRepository(ABC):
    """待注入当前 turn 的用户引导消息队列。"""

    @abstractmethod
    async def enqueue(self, user_id: str, session_id: str, query: str) -> None: ...

    @abstractmethod
    async def drain_all(self, user_id: str, session_id: str) -> List[str]: ...
