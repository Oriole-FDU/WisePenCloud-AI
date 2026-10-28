"""OpenAlex HTTP 适配；用户密钥只随当前请求发送，不绑定共享客户端。"""

from __future__ import annotations

import asyncio
from datetime import date
from urllib.parse import urlsplit

import httpx

from scholar.domain.errors import (
    ProviderError,
    provider_error_message,
)
from scholar.domain.interfaces.paper_lookup import PaperLookupMatch
from scholar.domain.interfaces.paper_searcher import PaperSearchBatch
from scholar.domain.models.fields import AcademicField
from scholar.domain.models.identifiers import (
    IdentifierKind,
    PaperIdentifier,
    normalize_arxiv,
    normalize_doi,
)
from scholar.domain.models.paper import Paper, PaperSource
from scholar.providers.field_mapping import OPENALEX_FILTERS
from scholar.providers.projection import project_paper

_FIELDS = (
    "id,doi,primary_location,best_oa_location,locations,updated_date,ids,"
    "cited_by_count,primary_topic,open_access,is_retracted,mesh,study_designs,"
    "title,abstract_inverted_index,authorships,publication_date,publication_year"
)


class OpenAlexProvider:
    """将统一搜索能力和精确 lookup 请求映射到 OpenAlex HTTP API。"""

    def __init__(self, http: httpx.AsyncClient):
        self.http = http
        self._slots = asyncio.Semaphore(4)

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
        if not api_key:
            raise ProviderError("OpenAlex API key is required")
        field_filter = OPENALEX_FILTERS[field]
        filters = [field_filter] if field_filter else []
        if published_after:
            filters.append(f"from_publication_date:{published_after.isoformat()}")
        if published_before:
            filters.append(f"to_publication_date:{published_before.isoformat()}")
        params = {"search": query, "per_page": limit, "select": _FIELDS}
        if filters:
            params["filter"] = ",".join(filters)
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        try:
            response = await self.http.get(
                "https://api.openalex.org/works", params=params, headers=headers
            )
            response.raise_for_status()
        except httpx.HTTPError as error:
            # 异常文本保留状态码与 URL；凭证仅在请求头中，不进入异常文本。
            raise ProviderError(provider_error_message(error)) from error
        return PaperSearchBatch(
            [project_paper(self.to_paper(work)) for work in response.json()["results"]]
        )

    async def lookup(
        self,
        ids: list[PaperIdentifier],
        *,
        api_key: str | None = None,
    ) -> list[PaperLookupMatch]:
        """批量精确读取完整 Work，保持输入顺序并约束并发。"""
        if not api_key:
            raise ProviderError("OpenAlex API key is required")

        async def resolve(identifier):
            async with self._slots:
                work = await self._fetch_work(identifier, api_key=api_key)
            if work is None:
                return PaperLookupMatch([])
            return PaperLookupMatch([self.to_paper(work)])

        tasks = [asyncio.create_task(resolve(identifier)) for identifier in ids]
        try:
            return list(await asyncio.gather(*tasks))
        finally:
            # 一个请求失败或调用者取消时，批次的其余 HTTP 不得游离在用例生命周期之外。
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _fetch_work(
        self, identifier: PaperIdentifier, *, api_key: str
    ) -> dict | None:
        # 单篇 endpoint 按 DOI 解析 work，不依赖列表中被截断的位置数组；
        # 适配器限制单篇 HTTP 并发，应用用例负责来源之间的回退和失败隔离。
        if identifier.kind == IdentifierKind.OPENALEX:
            key = identifier.value
        elif identifier.kind == IdentifierKind.DOI:
            key = f"https://doi.org/{identifier.value}"
        elif identifier.kind == IdentifierKind.ARXIV:
            # arXiv 官方为每篇论文定义 10.48550/arXiv.<ID>，并非由标题猜测 DOI。
            key = f"https://doi.org/10.48550/arxiv.{identifier.value}"
        else:
            raise ValueError("OpenAlex does not resolve this identifier kind")
        try:
            response = await self.http.get(
                f"https://api.openalex.org/works/{key}",
                params={"select": _FIELDS},
                headers={"Authorization": f"Bearer {api_key}"},
            )
            if response.status_code == 404:
                return None
            response.raise_for_status()
        except httpx.HTTPError as error:
            raise ProviderError(provider_error_message(error)) from error
        return response.json()

    @staticmethod
    def to_paper(work: dict) -> Paper:
        abstract_index = work["abstract_inverted_index"]
        abstract = None
        if abstract_index is not None:
            words = sorted(
                (position, word)
                for word, positions in abstract_index.items()
                for position in positions
            )
            abstract = " ".join(word for _, word in words)
        doi = normalize_doi(work["doi"]) if work["doi"] else None
        arxiv_ids = OpenAlexProvider._arxiv_ids(work, doi)
        arxiv_id = OpenAlexProvider._arxiv_id(arxiv_ids)
        primary = work["primary_location"]
        oa = work["best_oa_location"]
        return Paper(
            arxiv_id=arxiv_id,
            doi=doi,
            title=work["title"],
            abstract=abstract,
            authors=[entry["author"]["display_name"] for entry in work["authorships"]],
            publication_date=date.fromisoformat(work["publication_date"])
            if work["publication_date"]
            else None,
            year=work["publication_year"],
            url=(primary["landing_page_url"] if primary else None) or work["id"],
            pdf_url=oa["pdf_url"] if oa else None,
            source=PaperSource.OPENALEX,
            metadata={
                "openalex": OpenAlexProvider.metadata(work, primary, oa, arxiv_ids)
            },
        )

    @staticmethod
    def _arxiv_ids(work: dict, doi: str | None) -> set[str]:
        arxiv_ids = set()
        for location in work["locations"]:
            landing = location["landing_page_url"]
            if landing and urlsplit(landing).hostname in (
                "arxiv.org",
                "www.arxiv.org",
                "export.arxiv.org",
            ):
                arxiv_ids.add(normalize_arxiv(landing))
        if doi and doi.startswith("10.48550/arxiv."):
            arxiv_ids.add(normalize_arxiv(doi.removeprefix("10.48550/arxiv.")))
        return arxiv_ids

    @staticmethod
    def _arxiv_id(arxiv_ids: set[str]) -> str | None:
        # 上游一个 work 若包含不同 arXiv 身份，不任意选一个用于跨源合并。
        return next(iter(arxiv_ids)) if len(arxiv_ids) == 1 else None

    @staticmethod
    def metadata(
        work: dict, primary: dict | None, oa: dict | None, arxiv_ids: set[str]
    ) -> dict:
        """仅投影有消费价值的字段；空值不补造，研究设计保留其自动分类语义。"""
        values = {
            name: work.get(name)
            for name in (
                "id",
                "ids",
                "updated_date",
                "cited_by_count",
                "primary_topic",
                "open_access",
                "is_retracted",
                "mesh",
                "study_designs",
            )
        }
        values.update(
            {
                "venue": primary["source"]["display_name"]
                if primary and primary["source"]
                else None,
                "arxiv_ids": sorted(arxiv_ids),
                "best_oa_location": None
                if oa is None
                else {
                    key: oa.get(key)
                    for key in ("landing_page_url", "pdf_url", "license", "version")
                },
            }
        )
        return values
