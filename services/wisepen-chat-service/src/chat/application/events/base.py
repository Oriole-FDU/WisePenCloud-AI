from dataclasses import dataclass


@dataclass(frozen=True)
class StreamEvent:
    """应用层流式事件基类"""

    pass

@dataclass(frozen=True)
class ErrorEvent(StreamEvent):
    """错误事件"""
    error_text: str