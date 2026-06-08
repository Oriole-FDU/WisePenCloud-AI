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
class CrawlFrontierItem:
    """Frontier 调度单元，表示一个待抓取的 URL 及其元信息。"""
    url: str
    depth: int
    origin_host: str
    current_host: str
    source_url: Optional[str] = None
    anchor_text: Optional[str] = None
    surrounding_text: Optional[str] = None
    score: float = 0.0
    is_external: bool = False
    external_depth: int = 0
