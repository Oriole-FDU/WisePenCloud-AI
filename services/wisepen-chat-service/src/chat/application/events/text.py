from dataclasses import dataclass

from chat.application.events.base import StreamEvent


@dataclass(frozen=True)
class TextStartEvent(StreamEvent):
    """普通文本流开始。"""

    text_id: str


@dataclass(frozen=True)
class TextDeltaEvent(StreamEvent):
    """普通文本流增量。"""

    text_id: str
    delta: str


@dataclass(frozen=True)
class TextEndEvent(StreamEvent):
    """普通文本流结束。"""

    text_id: str
