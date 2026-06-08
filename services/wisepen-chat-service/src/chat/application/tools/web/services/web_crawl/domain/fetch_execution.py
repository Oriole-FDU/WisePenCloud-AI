from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from chat.application.tools.web.services.web_crawl.domain.frontier_scheduling import (
    CrawlFrontierItem,
)
from chat.application.tools.web.services.web_crawl.enums import CrawlItemKind
from chat.application.tools.web.services.web_crawl.models import CrawlResultItem


@dataclass(frozen=True, slots=True)
class PreFetchDecision:
    """预抓取决策结果，包含是否允许抓取及拒绝原因。"""
    allowed: bool
    item: CrawlFrontierItem
    skip_reason: Optional[str] = None
    error: Optional[str] = None

    def to_result_item(self) -> CrawlResultItem:
        """将拒绝决策转换为跳过条目。"""
        return CrawlResultItem(
            url=self.item.url,
            kind=CrawlItemKind.SKIPPED.value,
            depth=self.item.depth,
            success=False,
            source_url=self.item.source_url,
            error=self.error,
            skip_reason=self.skip_reason,
        )


@dataclass(frozen=True, slots=True)
class HandleFetchResult:
    """抓取结果处理结果，包含生成的条目及统计数据。"""
    items: List[CrawlResultItem]
    fetched_pages: int = 0
    documents_found: int = 0
    skipped_count: int = 0


@dataclass(frozen=True, slots=True)
class DiscoveryResult:
    """链接发现结果，包含跳过的条目计数。"""
    items: List[CrawlResultItem]
    skipped_count: int = 0