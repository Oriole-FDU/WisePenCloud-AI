from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from chat.domain.entities import ChatMessage, TurnCancelSignal
from chat.domain.interfaces.llm import LLMStreamEvent

@dataclass
class ExecutionBindings:
    """單次執行的程序內依賴，不寫入工具 context 或掛起快照。"""

    cancel_requested: Callable[[], Awaitable[TurnCancelSignal | None]] | None = None
    tool_handlers: dict[str, Callable[..., Awaitable[Any]]] = field(default_factory=dict)
    join_children: Callable[[], Awaitable[list[ChatMessage]]] | None = None
    close_children: Callable[[], Awaitable[None]] | None = None
    before_step: Callable[[], Awaitable[None]] | None = None
    record_usage: Callable[..., Awaitable[None]] | None = None
    snapshot_subagents: Callable[[], Awaitable[dict]] | None = None
    lease_lost: Callable[[], bool] | None = None
    review_model_output: Callable[[AsyncGenerator[LLMStreamEvent, None]], AsyncGenerator[LLMStreamEvent, None]] | None = None

    async def get_cancel_signal(self) -> TurnCancelSignal | None:
        return await self.cancel_requested() if self.cancel_requested is not None else None

    async def is_cancel_requested(self) -> bool:
        return await self.get_cancel_signal() is not None
