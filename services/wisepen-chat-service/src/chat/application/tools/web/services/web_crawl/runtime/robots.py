from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Dict, Optional
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import httpx

from chat.application.tools.web.services.web_crawl.enums import RobotsDecisionReason

_ROBOTS_CACHE_TTL_SECONDS = 30 * 60


@dataclass(frozen=True, slots=True)
class RobotsDecision:
    """Robots.txt 的查询决策结果，包含是否允许及建议延迟。"""
    allowed: bool
    unavailable: bool = False
    reason: Optional[RobotsDecisionReason] = None
    crawl_delay: Optional[float] = None


@dataclass(slots=True)
class RobotsCacheEntry:
    """Robots.txt 缓存条目，包含解析器、获取时间和不可用标记。"""
    parser: Optional[RobotFileParser]
    fetched_at: float
    unavailable: bool


class RobotsPolicy:
    """Robots.txt 爬取策略，负责获取、缓存和查询 robots.txt 规则。"""

    def __init__(self, *, timeout_seconds: float = 5.0):
        """初始化 HTTP 超时时间和 robots.txt 缓存字典。"""
        self._timeout_seconds = timeout_seconds
        self._cache: Dict[str, RobotsCacheEntry] = {}

    async def can_fetch(
        self,
        *,
        url: str,
        is_seed_url: bool,
        user_agent: str = "WisePenBot",
    ) -> RobotsDecision:
        """查询指定 URL 是否允许被抓取。

        若 robots.txt 不可用，种子 URL 放行，非种子 URL 拒绝。
        """
        parsed = urlparse(url)
        host_key = f"{parsed.scheme}://{parsed.netloc}".lower()
        entry = await self._get_or_fetch(host_key, user_agent=user_agent)

        if entry.unavailable:
            if is_seed_url:
                return RobotsDecision(
                    allowed=True,
                    unavailable=True,
                    reason=RobotsDecisionReason.UNAVAILABLE_SEED_ALLOWED,
                )
            return RobotsDecision(
                allowed=False,
                unavailable=True,
                reason=RobotsDecisionReason.UNAVAILABLE,
            )

        if entry.parser is None:
            return RobotsDecision(
                allowed=False,
                unavailable=True,
                reason=RobotsDecisionReason.UNAVAILABLE,
            )

        allowed = entry.parser.can_fetch(user_agent, url)
        delay = entry.parser.crawl_delay(user_agent)
        return RobotsDecision(
            allowed=allowed,
            reason=None if allowed else RobotsDecisionReason.DISALLOWED,
            crawl_delay=float(delay) if delay is not None else None,
        )

    async def _get_or_fetch(
        self,
        host_key: str,
        *,
        user_agent: str,
    ) -> RobotsCacheEntry:
        """获取或从远端抓取并缓存指定主机的 robots.txt 内容。"""
        now = time.time()
        cached = self._cache.get(host_key)
        if cached is not None and now - cached.fetched_at <= _ROBOTS_CACHE_TTL_SECONDS:
            return cached

        robots_url = f"{host_key}/robots.txt"
        try:
            async with httpx.AsyncClient(timeout=self._timeout_seconds) as client:
                response = await client.get(robots_url, headers={"User-Agent": user_agent})
            if response.status_code >= 400:
                entry = RobotsCacheEntry(parser=None, fetched_at=now, unavailable=True)
            else:
                parser = RobotFileParser()
                parser.set_url(robots_url)
                parser.parse(response.text.splitlines())
                entry = RobotsCacheEntry(parser=parser, fetched_at=now, unavailable=False)
        except Exception:
            entry = RobotsCacheEntry(parser=None, fetched_at=now, unavailable=True)

        self._cache[host_key] = entry
        return entry