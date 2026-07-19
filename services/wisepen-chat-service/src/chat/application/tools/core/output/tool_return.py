from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Mapping


class CacheableTextContentType(StrEnum):
    """可缓存正文支持的分块格式。"""

    PLAIN_TEXT = "text/plain"
    MARKDOWN = "text/markdown"


@dataclass(frozen=True, slots=True)
class CacheableText:
    """一段待治理正文及其分块所需的内容类型。"""

    text: str
    content_type: CacheableTextContentType = CacheableTextContentType.PLAIN_TEXT


@dataclass(frozen=True, slots=True, kw_only=True)
class ToolReturn:
    """工具成功执行后的结构化输出与待治理正文。"""

    visible_result: Mapping[str, Any] = field(default_factory=dict)
    cacheable_texts: tuple[CacheableText, ...] = ()
