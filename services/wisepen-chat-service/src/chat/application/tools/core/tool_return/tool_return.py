from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Any


@dataclass(frozen=True, slots=True, kw_only=True)
class ToolReturn:
    """工具执行结果的运行时信封，包含结构化可见输出与可缓存的富文本。"""

    tag: str  # 根节点 XML 标签名称
    visible_result: Mapping[str, Any] = field(default_factory=dict)  # 对模型直接可见的结构化载荷
    cacheable_texts: tuple[str, ...] = ()  # 触发动态存仓治理的大文本内容块
