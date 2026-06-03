from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional


@dataclass(frozen=True, slots=True)
class CrawlRequest:
    """爬取任务请求，包含种子 URL、目标描述及预算限制。"""
    user_id: str
    session_id: str
    seed_urls: List[str]
    objective: str
    max_depth: int = 1
    max_pages: int = 8


@dataclass(frozen=True, slots=True)
class CrawlResultItem:
    """爬取结果中的单个条目，包含 URL、类型、深度、内容和状态。"""
    url: str
    kind: str
    depth: int
    success: bool
    content_block: Optional[str] = None
    file_ref: Optional[str] = None
    source_url: Optional[str] = None
    error: Optional[str] = None
    skip_reason: Optional[str] = None


@dataclass(frozen=True, slots=True)
class CrawlResult:
    """一次完整的爬取结果汇总，包含所有条目及预算消耗情况。"""
    objective: str
    seed_urls: List[str]
    items: List[CrawlResultItem]
    fetched_pages: int
    documents_found: int
    skipped_count: int
    max_depth: int
    max_pages: int
    crawl_budget_exhausted: bool = False