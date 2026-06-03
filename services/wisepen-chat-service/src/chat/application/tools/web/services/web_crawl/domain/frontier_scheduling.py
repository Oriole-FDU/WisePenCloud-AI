from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


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