from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True, slots=True)
class LinkCandidate:
    """链接发现阶段候选 URL，包含锚文本、上下文及来源标题等信息。"""
    id: str
    url: str
    anchor_text: str
    surrounding_text: str
    source_title: str
    source_url: str
    depth: int
    origin_host: str
    current_host: str
    is_external: bool
    external_depth: int


@dataclass(frozen=True, slots=True)
class RankedLinkCandidate:
    """经过 BM25 排序后的链接候选，包含相关性分数和是否接受标记。"""
    candidate: LinkCandidate
    score: float
    accepted: bool
    reject_reason: Optional[str] = None