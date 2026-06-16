from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Any


@dataclass(frozen=True, slots=True, kw_only=True)
class ToolReturn:
    """Runtime envelope for tools that need structured visible output plus cached text."""

    tag: str
    visible_result: Mapping[str, Any] = field(default_factory=dict)
    cacheable_texts: tuple[str, ...] = ()
