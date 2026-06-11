from dataclasses import dataclass, field
from typing import List, Optional

from chat.application.events.base import StreamEvent
from chat.domain.entities import ChatMessage


@dataclass(frozen=True)
class StepStartEvent(StreamEvent):
    """一个 agent step 开始"""

    pass


@dataclass(frozen=True)
class StepFinishEvent(StreamEvent):
    """一个 agent step 结束"""
    is_finished: bool
    intermediate_messages: List[ChatMessage] = field(default_factory=list)
    final_assistant_message: Optional[ChatMessage] = None
    usage_tokens: int = field(default_factory=int)
