from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

from chat.application.tools.web.services.web_crawl.enums import CrawlSkipReason
from chat.application.tools.web.services.web_crawl.runtime.models import CrawlFrontierItem
from chat.application.tools.web.utils.domains import extract_domain


@dataclass(slots=True)
class CrawlScopeState:
    """Crawl 作用域状态，主要用于限制外链扩散：

    - origin_hosts: seed_urls 所属 host 集合，作为站内/站外判断基准。
    - max_external_pages_total: 整个任务最多允许多少外链页面入队。
    - max_pages_per_external_host: 单个外部 host 最多允许多少页面入队。
    - max_external_depth: 外链继续向外扩展的最大深度。
    - external_pages_total / pages_by_external_host: 实际出队抓取后的外链统计。
    """

    origin_hosts: Set[str]
    max_external_pages_total: int
    max_pages_per_external_host: int = 2
    max_external_depth: int = 1
    external_pages_total: int = 0
    pages_by_external_host: Dict[str, int] = field(default_factory=dict)


class CrawlFrontier:
    """Crawl URL 调度池。

    核心职责：
    - 管理待抓 URL 队列。
    - 控制 max_pages / max_depth。
    - 控制 URL 去重。
    - 控制外链扩散预算。
    - 按 depth、score、host 多样性弹出下一批 URL。
    """

    def __init__(
        self,
        *,
        seed_urls: List[str],
        max_pages: int,
        max_depth: int,
    ):
        """初始化页面预算、深度限制、去重集合和分层待抓队列。"""
        self._max_pages = max_pages
        self._max_depth = max_depth

        # 已出队并进入抓取流程的 URL，避免重复抓取
        self._visited: Set[str] = set()

        # 已进入 frontier 的 URL，避免重复入队
        self._queued: Set[str] = set()

        # depth -> 待抓 URL 列表，优先弹出浅层 depth，同层内按 score 降序
        self._pending_by_depth: Dict[int, List[CrawlFrontierItem]] = defaultdict(list)

        # 已从 frontier 弹出、准备实际抓取的页面数，用于控制 max_pages 预算
        self._accepted_count = 0

        # 外链入队预算计数，用于防止外链先把 pending 队列占满
        self._queued_external_total = 0
        self._queued_by_external_host: Dict[str, int] = {}

        origin_hosts = {host for url in seed_urls for host in [extract_domain(url)] if host}
        self.scope = CrawlScopeState(
            origin_hosts=origin_hosts,
            max_external_pages_total=min(5, max_pages // 2),
        )

        # 将种子 URL 入队，depth 为 0
        for url in seed_urls:
            host = extract_domain(url)
            if url not in self._queued:
                self._queued.add(url)
                self._pending_by_depth[0].append(
                    CrawlFrontierItem(
                        url=url,
                        depth=0,
                        origin_host=host,
                        current_host=host,
                        score=1.0,
                        is_external=False,
                        external_depth=0,
                    )
                )

    def add_candidate(self, item: CrawlFrontierItem) -> Tuple[bool, Optional[CrawlSkipReason]]:
        """尝试把新发现的 URL 加入 frontier。

        入队检查顺序：
        1. depth 不能超过 max_depth。
        2. accepted_count 不能超过 max_pages。
        3. URL 不能已经 visited / queued。
        4. 外链必须满足 external budget。
        """

        if item.depth > self._max_depth:
            return False, CrawlSkipReason.DEPTH_LIMIT

        if self.reached_page_budget():
            return False, CrawlSkipReason.PAGE_LIMIT

        if item.url in self._visited or item.url in self._queued:
            return False, CrawlSkipReason.DUPLICATE_URL

        ok, reason = self._check_external_budget(item)
        if not ok:
            return False, reason

        self._queued.add(item.url)
        self._pending_by_depth[item.depth].append(item)
        self._mark_scope_queued(item)
        return True, None

    def pop_next_batch(
        self,
        *,
        limit: int,
        excluded_hosts: Optional[Set[str]] = None,
    ) -> List[CrawlFrontierItem]:
        """弹出下一批待抓 URL。

        关键调度算法：
        - 全局不超过 remaining page budget。
        - depth 从小到大，优先浅层页面。
        - 同一 depth 内按 score 降序。
        - 单个 batch 内不重复选择同一个 host，避免瞬时打爆单站。
        - excluded_hosts 用于跳过当前轮暂时不应该抓的 host。
        """

        if limit <= 0:
            return []

        excluded_hosts = excluded_hosts or set()
        remaining_budget = max(0, self._max_pages - self._accepted_count)
        if remaining_budget <= 0:
            return []

        selected: List[CrawlFrontierItem] = []
        selected_hosts: Set[str] = set()
        limit = min(limit, remaining_budget)

        for depth in sorted(self._pending_by_depth):
            if len(selected) >= limit:
                break

            items = self._pending_by_depth[depth]
            items.sort(key=lambda item: item.score, reverse=True)
            remaining: List[CrawlFrontierItem] = []

            for item in items:
                host = item.current_host or extract_domain(item.url)
                if (
                    len(selected) < limit
                    and host not in excluded_hosts
                    and host not in selected_hosts
                ):
                    selected.append(item)
                    selected_hosts.add(host)
                    continue

                remaining.append(item)

            if remaining:
                self._pending_by_depth[depth] = remaining
            else:
                self._pending_by_depth.pop(depth, None)

        for item in selected:
            self._visited.add(item.url)
            self._accepted_count += 1
            self._mark_scope_fetched(item)

        return selected

    def reached_page_budget(self) -> bool:
        """检查是否已达最大抓取页面预算。"""
        return self._accepted_count >= self._max_pages

    def has_pending(self) -> bool:
        """检查是否还有待抓取的 URL。"""
        return any(self._pending_by_depth.values())

    def _check_external_budget(
        self, item: CrawlFrontierItem
    ) -> Tuple[bool, Optional[CrawlSkipReason]]:
        """检查外链是否还能入队。

        外链预算只约束 is_external=True 的 item：
        - external_depth 不能超过 max_external_depth。
        - 外链总入队数不能超过 max_external_pages_total。
        - 单个外部 host 入队数不能超过 max_pages_per_external_host。
        """

        if not item.is_external:
            return True, None

        if item.external_depth > self.scope.max_external_depth:
            return False, CrawlSkipReason.EXTERNAL_DEPTH_LIMIT

        if self._queued_external_total >= self.scope.max_external_pages_total:
            return False, CrawlSkipReason.EXTERNAL_BUDGET_EXCEEDED

        host_count = self._queued_by_external_host.get(item.current_host, 0)
        if host_count >= self.scope.max_pages_per_external_host:
            return False, CrawlSkipReason.EXTERNAL_HOST_BUDGET_EXCEEDED

        return True, None

    def _mark_scope_queued(self, item: CrawlFrontierItem) -> None:
        """标记外链已入队，消耗对应的外链入队预算。"""
        if not item.is_external:
            return

        self._queued_external_total += 1
        self._queued_by_external_host[item.current_host] = (
            self._queued_by_external_host.get(item.current_host, 0) + 1
        )

    def _mark_scope_fetched(self, item: CrawlFrontierItem) -> None:
        """标记外链已出队抓取，只用于统计，不参与入队预算判断。"""
        if not item.is_external:
            return

        self.scope.external_pages_total += 1
        self.scope.pages_by_external_host[item.current_host] = (
            self.scope.pages_by_external_host.get(item.current_host, 0) + 1
        )