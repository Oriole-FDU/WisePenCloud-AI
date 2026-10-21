from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from chat.domain.entities import ChatMessage

@dataclass
class ExecutionBindings:
    """單次執行的程序內依賴，不寫入工具 context 或掛起快照。"""

    cancel_requested: Callable[[], Awaitable[bool]] | None = None
    tool_handlers: dict[str, Callable[..., Awaitable[Any]]] = field(default_factory=dict)
    join_children: Callable[[], Awaitable[list[ChatMessage]]] | None = None
    close_children: Callable[[], Awaitable[None]] | None = None
    before_step: Callable[[], Awaitable[None]] | None = None
    record_usage: Callable[..., Awaitable[None]] | None = None
    snapshot_subagents: Callable[[], Awaitable[dict]] | None = None
    lease_lost: Callable[[], bool] | None = None

    async def is_cancel_requested(self) -> bool:
        return self.cancel_requested is not None and await self.cancel_requested()
