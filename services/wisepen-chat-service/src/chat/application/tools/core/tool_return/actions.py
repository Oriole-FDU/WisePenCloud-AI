from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class SuggestedActionPriority(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"

@dataclass(frozen=True, slots=True)
class SuggestedAction:
    tool_name: str
    reason: str
    mode: str | None = None
    priority: SuggestedActionPriority = SuggestedActionPriority.MEDIUM
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class SuggestedActions:
    suggested_actions: tuple[SuggestedAction, ...] = ()
    notice: str = (
        "Suggested actions are optional hints. They identify tools and route-level "
        "modes that may help, but they are not mandatory instructions or complete "
        "tool-call arguments."
    )
