from dataclasses import dataclass, field
from typing import List, Optional

from chat.application.events.base import StreamEvent
from chat.application.tools.core import ClassifiedToolInvocationPlan
from chat.domain.entities import ChatMessage
from chat.domain.interfaces.llm import TokenUsage


@dataclass(frozen=True)
class StepStartEvent(StreamEvent):
    """一个 agent step 开始"""

    pass


@dataclass(frozen=False)
class TurnSuspension:
    classified_tool_invocation_plan: ClassifiedToolInvocationPlan
    iteration: int

    @property
    def next_iteration(self) -> int:
        return self.iteration + 1

@dataclass(frozen=True)
class StepFinishEvent(StreamEvent):
    """一个 agent step 结束"""
    is_finished: bool
    intermediate_messages: List[ChatMessage] = field(default_factory=list)
    final_assistant_message: Optional[ChatMessage] = None
    token_usage: TokenUsage = field(default_factory=TokenUsage)
    suspension: Optional[TurnSuspension] = None
    aborted: bool = False
