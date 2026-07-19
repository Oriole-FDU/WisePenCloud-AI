from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


@dataclass(frozen=True, slots=True, kw_only=True)
class ToolReturn:
    """工具成功执行后的结构化输出与待治理正文。"""

    visible_result: Mapping[str, Any] = field(default_factory=dict)
    cacheable_texts: tuple[str, ...] = ()
