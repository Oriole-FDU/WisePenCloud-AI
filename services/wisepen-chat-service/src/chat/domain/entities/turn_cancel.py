from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from enum import Enum


class TurnCancelReason(str, Enum):

    USER = "user"
    CONTENT_SAFETY = "content_safety"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class TurnCancelSignal:

    reason: TurnCancelReason
    meta: dict = field(default_factory=dict)

    def to_redis(self) -> str:
        return json.dumps(
            {"reason": self.reason.value, "meta": self.meta},
            ensure_ascii=False,
        )

    @classmethod
    def from_redis(cls, raw: str | None) -> "TurnCancelSignal | None":
        if raw is None: return None
        try:
            data = json.loads(raw)
            reason = TurnCancelReason(data.get("reason"))
            meta = data.get("meta")
            return cls(reason=reason, meta=meta)
        except (json.JSONDecodeError, TypeError, ValueError, AttributeError):
            return cls(reason=TurnCancelReason.UNKNOWN)

class TurnCancelledError(asyncio.CancelledError):
    """携带取消信号的内部控制流异常，由 turn 取消检查点抛出。"""

    def __init__(self, signal: TurnCancelSignal) -> None:
        super().__init__()
        self.signal = signal
