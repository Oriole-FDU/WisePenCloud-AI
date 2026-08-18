"""导航状态持久化 port。"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Protocol


class NavigationStateMissingError(RuntimeError):
    """导航状态在持久化层不存在或已过期。"""


@dataclass(slots=True)
class NavigationState:
    """导航仓储保存的用户、会话和已发现节点状态。"""

    state_id: str
    user_id: str
    session_id: str
    known_node_ids: list[str] = field(default_factory=list)


class NavigationStateStore(Protocol):
    """创建、读取并原子扩展导航状态。"""

    async def create(
        self,
        *,
        user_id: str,
        session_id: str,
        known_node_ids: Sequence[str],
    ) -> NavigationState: ...

    async def get(self, state_id: str) -> NavigationState | None: ...

    async def add_known_nodes(
        self,
        *,
        state_id: str,
        node_ids: Sequence[str],
    ) -> list[str]: ...
