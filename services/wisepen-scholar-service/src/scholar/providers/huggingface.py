"""Hugging Face Papers 的异步 HTTP 适配与精选事实投影。"""

import asyncio
import calendar
from datetime import UTC, date, datetime, timedelta
from typing import Any

import httpx

from scholar.domain.interfaces.paper_discoverer import (
    DiscoveryPeriod,
    DiscoverySort,
    PaperDiscovery,
)
from scholar.domain.interfaces.paper_enricher import PaperEnrichment
from scholar.domain.interfaces.provider_error import (
    ProviderError,
    provider_error_message,
)
from scholar.domain.models.identifiers import (
    IdentifierKind,
    PaperIdentifier,
    parse_identifier,
)
from scholar.domain.models.paper import Paper, PaperSource

_HF_ORIGIN = "https://huggingface.co"


class HuggingFaceProvider:
    """用官方 Papers HTTP endpoints 提供逐篇 HF 富化和周期发现。"""

    def __init__(
        self,
        http: httpx.AsyncClient,
        *,
        token: str | None = None,
        concurrency: int = 4,
    ):
        self.http = http
        self._headers = {"Authorization": f"Bearer {token}"} if token else {}
        self._request_limit = asyncio.Semaphore(concurrency)

    async def enrich(
        self,
        identifiers: list[PaperIdentifier],
        *,
        include_details: bool = False,
    ) -> list[PaperEnrichment | None]:
        return await asyncio.gather(
            *(
                self._enrich_one(identifier, include_details=include_details)
                for identifier in identifiers
            )
        )

    async def _enrich_one(
        self, identifier: PaperIdentifier, *, include_details: bool
    ) -> PaperEnrichment | None:
        if identifier.kind != IdentifierKind.ARXIV:
            return None

        payload = await self._paper_info(identifier.value)
        if payload is None:
            return None

        observed_at = datetime.now(UTC).isoformat()
        metadata = self._metadata(payload, observed_at=observed_at)
        resolved_identifier = parse_identifier(f"arxiv:{self._paper_id(payload)}")
        return PaperEnrichment(
            identifiers=[resolved_identifier],
            metadata={"huggingface": metadata},
            paper=self._to_paper(payload, metadata) if include_details else None,
        )

    async def discover(
        self,
        *,
        period: DiscoveryPeriod,
        sort: DiscoverySort,
        limit: int,
    ) -> PaperDiscovery:
        if sort == DiscoverySort.TRENDING:
            # HF 的 trending 不应用周期筛选，不能将全局列表标成日、周或月结果。
            raise ProviderError(
                "Hugging Face Daily Papers does not support period-scoped trending"
            )

        now = datetime.now(UTC)
        params: dict[str, str | int] = {
            "p": 0,
            "limit": limit,
            "sort": "publishedAt",
        }
        window_start = None
        window_end = None
        if period == DiscoveryPeriod.DAILY:
            # 首条只确定最新渠道日期；第二次显式按日期取数，避免 limit 跨日。
            latest_response = await self._get(
                f"{_HF_ORIGIN}/api/daily_papers",
                params={"p": 0, "limit": 1, "sort": "publishedAt"},
            )
            latest_rows = latest_response.json()
            if not latest_rows:
                return PaperDiscovery(papers=[], window_start=None, window_end=None)

            latest_date = self._parse_date(self._submitted_at(latest_rows[0]))
            if latest_date is None:
                raise ProviderError(
                    "Hugging Face Daily Papers row is missing submittedOnDailyAt"
                )
            params["date"] = latest_date.isoformat()
        elif period == DiscoveryPeriod.WEEKLY:
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

        response = await self._get(f"{_HF_ORIGIN}/api/daily_papers", params=params)
        rows = response.json()
        submitted_dates = [self._parse_date(self._submitted_at(row)) for row in rows]
        if period == DiscoveryPeriod.DAILY:
            if any(value is None or value != latest_date for value in submitted_dates):
                raise ProviderError(
                    "Hugging Face Daily Papers returned rows outside the requested date"
                )
            window_start = window_end = latest_date
        elif rows and any(
            value is None or value < window_start or value > window_end
            for value in submitted_dates
        ):
            raise ProviderError(
                f"Daily Papers returned rows outside the requested {period} window"
            )

        observed_at = datetime.now(UTC).isoformat()
        papers = [
            self._to_paper(
                row, self._metadata(row, observed_at=observed_at, partial=True)
            )
            for row in rows
        ]

        return PaperDiscovery(
            papers=papers,
            window_start=window_start,
            window_end=window_end,
        )

    async def _paper_info(self, arxiv_id: str) -> dict[str, Any] | None:
        response = await self._get(
            f"{_HF_ORIGIN}/api/papers/{arxiv_id}", allow_not_found=True
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
    ) -> httpx.Response:
        # 详情 404 是未找到；其他 HTTPX 边界失败保留原文和 cause。
        try:
            async with self._request_limit:
                response = await self.http.get(
                    url, params=params, headers=self._headers
                )
            if not allow_not_found or response.status_code != 404:
                response.raise_for_status()
            return response
        except httpx.HTTPError as error:
            raise ProviderError(provider_error_message(error)) from error

    @classmethod
    def _metadata(
        cls, payload: dict[str, Any], *, observed_at: str, partial: bool = False
    ) -> dict[str, Any]:
        result = {
            "upvotes": cls._field(payload, "upvotes"),
            "github_repo": cls._field(payload, "githubRepo"),
            "github_stars": cls._field(payload, "githubStars"),
            "linked_models": cls._linked(payload, "linkedModels"),
            "num_total_models": cls._field(payload, "numTotalModels"),
            "linked_datasets": cls._linked(payload, "linkedDatasets"),
            "num_total_datasets": cls._field(payload, "numTotalDatasets"),
            "observed_at": observed_at,
            "submitted_at": cls._submitted_at(payload),
        }
        if partial:
            # 列表未提供的产物字段仍未查询；详情成功返回的空字段才是确认空事实。
            present = payload.keys() | payload.get("paper", {}).keys()
            for name, upstream in (
                ("upvotes", "upvotes"),
                ("github_repo", "githubRepo"),
                ("github_stars", "githubStars"),
                ("linked_models", "linkedModels"),
                ("num_total_models", "numTotalModels"),
                ("linked_datasets", "linkedDatasets"),
                ("num_total_datasets", "numTotalDatasets"),
            ):
                if upstream not in present:
                    result.pop(name)
        return result

    @staticmethod
    def _field(payload: dict[str, Any], key: str) -> Any:
        nested = payload.get("paper", {})
        return nested[key] if key in nested else payload.get(key)

    @classmethod
    def _linked(cls, payload: dict[str, Any], key: str) -> list[dict[str, Any]] | None:
        values = cls._field(payload, key)
        if values is None:
            return None
        return [{"id": item["id"], "likes": item.get("likes")} for item in values]

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
