"""按领域策略调用发现渠道，并缓存上游候选与显式请求范围。"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, date, datetime

import orjson

from common.core.exceptions import ServiceException
from scholar.application.field_policies import FieldPolicy
from scholar.application.services.pdf_recover import PaperPdfRecoverService
from scholar.domain.errors import ScholarErrorCode
from scholar.domain.interfaces.paper_discoverer import (
    DiscoveryPeriod,
    DiscoverySort,
    PaperDiscoverer,
)
from scholar.domain.models.fields import AcademicField
from scholar.application.paper_projection import project_paper
from scholar.domain.models.paper import Paper
from scholar.domain.models.source_execution import (
    SourceExecution,
    SourceExecutionStatus,
)
from scholar.domain.repositories.discovery_cache import DiscoveryCacheRepository


@dataclass(frozen=True)
class PaperDiscoveryQuery:
    field: AcademicField
    period: DiscoveryPeriod = DiscoveryPeriod.DAILY
    sort: DiscoverySort = DiscoverySort.TRENDING
    limit: int = 10


@dataclass
class DiscoveryResult:
    papers: list[Paper]
    window_start: date | None
    window_end: date | None
    execution: list[SourceExecution]


class PaperDiscoveryService:
    """选择已登记的 discovery 能力，复用其候选结果并报告本次执行状态。"""

    def __init__(
        self,
        discoverers: dict[str, PaperDiscoverer],
        policies: dict[AcademicField, FieldPolicy],
        cache: DiscoveryCacheRepository,
        pdf: PaperPdfRecoverService,
    ):
        self.discoverers = discoverers
        self.policies = policies
        self.cache = cache
        self.pdf = pdf

    async def discover(
        self, query: PaperDiscoveryQuery, *, credentials: dict[str, str] | None = None
    ) -> DiscoveryResult:
        source = self.policies[query.field].discoverer
        if source is None or source not in self.discoverers:
            raise ServiceException(ScholarErrorCode.CAPABILITY_UNAVAILABLE)

        api_key = (credentials or {}).get(source)
        if not api_key:
            raise ServiceException(ScholarErrorCode.CAPABILITY_UNAVAILABLE, f"{source} discovery requires user BYOK")
        cache_key = self._cache_key(source, query)
        discovery = await self.cache.get(cache_key)
        cache_hit = discovery is not None
        if discovery is None:
            # ProviderError、缓存错误和取消保留原语义；失败发现不会写负缓存。
            discovery = await self.discoverers[source].discover(
                period=query.period,
                sort=query.sort,
                limit=100,
                api_key=api_key,
            )
            await self.cache.put(cache_key, discovery)

        papers, pdf_execution = await self.pdf.recover(discovery.papers[: query.limit])
        execution = [SourceExecution(source, cache_hit, SourceExecutionStatus.COMPLETED)]
        if pdf_execution is not None:
            execution.append(pdf_execution)
        return DiscoveryResult(
            papers=[project_paper(paper) for paper in papers],
            window_start=discovery.window_start,
            window_end=discovery.window_end,
            execution=execution,
        )

    @staticmethod
    def _cache_key(source: str, query: PaperDiscoveryQuery) -> str:
        today = datetime.now(UTC).date()
        if query.period == DiscoveryPeriod.DAILY:
            window = today.isoformat()
        elif query.period == DiscoveryPeriod.WEEKLY:
            week = today.isocalendar()
            window = f"{week.year}-W{week.week:02d}"
        else:
            window = f"{today.year:04d}-{today.month:02d}"

        # 同一来源和发现窗口共享候选，调用凭证不参与缓存身份。
        identity = {
            "source": source,
            "field": query.field.value,
            "period": query.period.value,
            "window": window,
            "sort": query.sort.value,
            "candidates": 100,
        }
        digest = hashlib.sha256(
            orjson.dumps(identity, option=orjson.OPT_SORT_KEYS)
        ).hexdigest()
        return f"{source}:{digest}"
