"""Hugging Face Papers 的异步 HTTP 适配与精选事实投影。"""

from __future__ import annotations

import asyncio
import calendar
from datetime import UTC, date, datetime, timedelta
from typing import Any

import httpx

from scholar.domain.errors import (
    ProviderError,
    provider_error_message,
)
from scholar.domain.interfaces.paper_discoverer import (
    DiscoveryPeriod,
    DiscoverySort,
    PaperDiscovery,
)
from scholar.domain.interfaces.paper_lookup import PaperLookupMatch
from scholar.domain.interfaces.paper_searcher import PaperSearchBatch
from scholar.domain.models.fields import AcademicField
from scholar.domain.models.identifiers import (
    IdentifierKind,
    PaperIdentifier,
    parse_identifier,
)
from scholar.domain.models.paper import Paper, PaperSource
from scholar.providers.projection import project_paper

_HF_ORIGIN = "https://huggingface.co"


class HuggingFaceProvider:
    """用官方 Papers HTTP endpoints 提供搜索、逐篇富化和周期发现。"""

    def __init__(
        self,
        http: httpx.AsyncClient,
        *,
        token: str | None = None,
        concurrency: int = 4,
    ):
        self.http = http
        self._token = token
        self._request_limit = asyncio.Semaphore(concurrency)

    async def search(
        self,
        *,
        query: str,
        field: AcademicField,
        published_after: date | None,
        published_before: date | None,
        limit: int,
        api_key: str | None = None,
    ) -> PaperSearchBatch:
        # 官方搜索只有 q/limit；日期在前 100 个候选内过滤，不伪称完整日期召回。
        dated = published_after is not None or published_before is not None
        response = await self._get(
            f"{_HF_ORIGIN}/api/papers/search",
            params={"q": query, "limit": 100 if dated else limit},
            api_key=api_key,
        )
        papers = [
            project_paper(self._to_paper(row, self._metadata(row, partial=True)))
            for row in response.json()
        ]
        if dated:
            papers = [
                paper
                for paper in papers
                if paper.publication_date is not None
                and (
                    published_after is None or paper.publication_date >= published_after
                )
                and (
                    published_before is None
                    or paper.publication_date <= published_before
                )
            ]
        return PaperSearchBatch(papers[:limit])

    async def lookup(
        self,
        ids: list[PaperIdentifier],
        *,
        api_key: str | None = None,
    ) -> list[PaperLookupMatch]:
        async def resolve(identifier: PaperIdentifier) -> PaperLookupMatch:
            if identifier.kind != IdentifierKind.ARXIV:
                return PaperLookupMatch([])
            try:
                payload = await self._paper_info(identifier.value, api_key=api_key)
            except ProviderError as error:
                # _paper_info 的来源请求失败按论文隔离；取消和程序错误不会被包装。
                return PaperLookupMatch([], str(error))
            if payload is None:
                return PaperLookupMatch([])
            metadata = self._metadata(payload)
            return PaperLookupMatch([self._to_paper(payload, metadata)])

        tasks = [asyncio.create_task(resolve(identifier)) for identifier in ids]
        try:
            return list(await asyncio.gather(*tasks))
        finally:
            # 批次取消时同步结束已创建任务，避免 HTTP 请求脱离当前用例生命周期。
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def discover(
        self,
        *,
        period: DiscoveryPeriod,
        sort: DiscoverySort,
        limit: int,
        api_key: str | None = None,
    ) -> PaperDiscovery:
        now = datetime.now(UTC)
        params: dict[str, str | int] = {
            "p": 0,
            "limit": limit,
            # 与官方 HfApi.list_daily_papers 一致：透传筛选与排序，按上游顺序接收结果。
            "sort": "publishedAt" if sort == DiscoverySort.RECENT else "trending",
        }
        window_start = None
        window_end = None
        # 官方 date 默认取最近内容；daily 不额外探测日期或重发第二次请求。
        if period == DiscoveryPeriod.WEEKLY:
            iso_week = now.isocalendar()
            params["week"] = f"{iso_week.year}-W{iso_week.week:02d}"
            window_start = now.date() - timedelta(days=now.weekday())
            window_end = window_start + timedelta(days=6)
        elif period == DiscoveryPeriod.MONTHLY:
            window_start = now.date().replace(day=1)
            window_end = now.date().replace(
                day=calendar.monthrange(now.year, now.month)[1]
            )
            params["month"] = f"{now.year:04d}-{now.month:02d}"

        response = await self._get(
            f"{_HF_ORIGIN}/api/daily_papers", params=params, api_key=api_key
        )
        rows = response.json()
        papers = [
            project_paper(self._to_paper(row, self._metadata(row, partial=True)))
            for row in rows
        ]

        return PaperDiscovery(
            papers=papers,
            window_start=window_start,
            window_end=window_end,
        )

    async def _paper_info(
        self, arxiv_id: str, *, api_key: str | None = None
    ) -> dict[str, Any] | None:
        response = await self._get(
            f"{_HF_ORIGIN}/api/papers/{arxiv_id}", allow_not_found=True, api_key=api_key
        )
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.json()

    async def _get(
        self,
        url: str,
        *,
        params: dict[str, str | int] | None = None,
        allow_not_found: bool = False,
        api_key: str | None = None,
    ) -> httpx.Response:
        # 详情 404 是未找到；其他 HTTPX 边界失败保留原文和 cause。
        try:
            async with self._request_limit:
                response = await self.http.get(
                    url,
                    params=params,
                    headers={"Authorization": f"Bearer {api_key or self._token}"}
                    if (api_key or self._token)
                    else {},
                )
            if not allow_not_found or response.status_code != 404:
                response.raise_for_status()
            return response
        except httpx.HTTPError as error:
            raise ProviderError(provider_error_message(error)) from error

    @classmethod
    def _metadata(
        cls, payload: dict[str, Any], *, partial: bool = False
    ) -> dict[str, Any]:
        result = {
            # HF 的 AI 摘要是渠道增量，保留命名空间，不覆盖论文原摘要。
            "ai_summary": cls._field(payload, "ai_summary"),
            "ai_keywords": cls._field(payload, "ai_keywords"),
            "upvotes": cls._field(payload, "upvotes"),
            "github_repo": cls._field(payload, "githubRepo"),
            "github_stars": cls._field(payload, "githubStars"),
            "submitted_at": cls._submitted_at(payload),
        }
        # 点赞降序只保留前三项；Python 稳定排序保留同赞产物的上游次序。
        for name, upstream in (
            ("linked_models", "linkedModels"),
            ("linked_datasets", "linkedDatasets"),
        ):
            values = cls._field(payload, upstream)
            result[name] = (
                None
                if values is None
                else [
                    {"id": item["id"], "likes": item.get("likes")}
                    for item in sorted(
                        values,
                        key=lambda item: item.get("likes") or 0,
                        reverse=True,
                    )[:3]
                ]
            )
        if partial:
            # 列表未提供的产物字段仍未查询；详情成功返回的空字段才是确认空事实。
            present = payload.keys() | payload.get("paper", {}).keys()
            for name, upstream in (
                ("ai_summary", "ai_summary"),
                ("ai_keywords", "ai_keywords"),
                ("upvotes", "upvotes"),
                ("github_repo", "githubRepo"),
                ("github_stars", "githubStars"),
                ("linked_models", "linkedModels"),
                ("linked_datasets", "linkedDatasets"),
            ):
                if upstream not in present:
                    result.pop(name)
        return result

    @staticmethod
    def _field(payload: dict[str, Any], key: str) -> Any:
        nested = payload.get("paper", {})
        return nested[key] if key in nested else payload.get(key)

    @staticmethod
    def _paper_id(payload: dict[str, Any]) -> str:
        if payload.get("id") is not None:
            return payload["id"]
        return payload["paper"]["id"]

    @staticmethod
    def _published_at(payload: dict[str, Any]) -> str | None:
        nested = payload.get("paper", {})
        return nested.get("publishedAt") or payload.get("publishedAt")

    @staticmethod
    def _submitted_at(payload: dict[str, Any]) -> str | None:
        # Daily Papers 的渠道分发时间由 submittedOnDailyAt 表示，publishedAt 是论文日期。
        nested = payload.get("paper", {})
        return nested.get("submittedOnDailyAt") or payload.get("submittedOnDailyAt")

    @classmethod
    def _to_paper(cls, payload: dict[str, Any], metadata: dict[str, Any]) -> Paper:
        arxiv_id = parse_identifier(f"arxiv:{cls._paper_id(payload)}").value
        authors = cls._field(payload, "authors")
        publication_date = cls._parse_date(cls._published_at(payload))
        return Paper(
            arxiv_id=arxiv_id,
            doi=None,
            title=cls._field(payload, "title"),
            abstract=cls._field(payload, "summary"),
            authors=[] if authors is None else [author["name"] for author in authors],
            publication_date=publication_date,
            year=None if publication_date is None else publication_date.year,
            url=f"https://arxiv.org/abs/{arxiv_id}",
            pdf_url=f"https://arxiv.org/pdf/{arxiv_id}",
            source=PaperSource.HUGGINGFACE,
            metadata={"huggingface": metadata},
        )

    @staticmethod
    def _parse_date(value: str | None) -> date | None:
        if value is None:
            return None
        return datetime.fromisoformat(value).date()
