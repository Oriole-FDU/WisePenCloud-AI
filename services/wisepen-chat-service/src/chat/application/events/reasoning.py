from dataclasses import dataclass

from chat.application.events.base import StreamEvent


@dataclass(frozen=True)
class ReasoningStartEvent(StreamEvent):
    """推理文本流开始"""

    reasoning_id: str


@dataclass(frozen=True)
class ReasoningDeltaEvent(StreamEvent):
    """推理文本流增量"""

    reasoning_id: str
    delta: str


@dataclass(frozen=True)
class ReasoningEndEvent(StreamEvent):
    """推理文本流结束"""

    reasoning_id: str
