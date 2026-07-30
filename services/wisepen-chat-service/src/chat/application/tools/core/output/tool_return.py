from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


@dataclass(frozen=True, slots=True)
class CacheableText:
    """一段待治理正文，以及内容格式和稳定来源标识。"""

    text: str
    is_md: bool = False
    metadata: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True, kw_only=True)
class ToolReturn:
    """工具成功执行后的结构化输出与待治理正文。"""

    visible_result: Mapping[str, Any] = field(default_factory=dict)
    cacheable_texts: tuple[CacheableText, ...] = ()
