from dataclasses import dataclass
from typing import Any

from chat.application.events.base import StreamEvent


@dataclass(frozen=True)
class ToolInputStartEvent(StreamEvent):
    """工具调用输入阶段开始。"""

    call_id: str
    tool_name: str


@dataclass(frozen=True)
class ToolInputAvailableEvent(StreamEvent):
    """工具调用输入已完整可用。"""

    call_id: str
    tool_name: str
    input: dict[str, Any]


@dataclass(frozen=True)
class ToolOutputAvailableEvent(StreamEvent):
    """工具调用输出已可用。"""

    call_id: str
    output: Any
