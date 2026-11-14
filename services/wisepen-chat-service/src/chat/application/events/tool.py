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
class ToolInputDeltaEvent(StreamEvent):
    """工具调用参数的一段原始 JSON 字符串增量。"""
    call_id: str
    delta: str


@dataclass(frozen=True)
class ToolOutputAvailableEvent(StreamEvent):
    """工具调用输出已可用。"""
    call_id: str
    output: str


@dataclass(frozen=True)
class ToolErrorEvent(StreamEvent):
    """工具输出失败。"""
    call_id: str
    error_text: str


@dataclass(frozen=True)
class ToolDeniedEvent(StreamEvent):
    """工具调用被用户拒绝。"""
    call_id: str


@dataclass(frozen=True)
class ToolApprovalRequiredEvent(StreamEvent):
    """高危工具需要用户审批。"""
    call_id: str
    tool_name: str
    input: dict[str, Any]
    tool_desc: str
