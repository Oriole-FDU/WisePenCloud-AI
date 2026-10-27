"""OpenAlex HTTP 适配；用户密钥只随当前请求发送，不绑定共享客户端。"""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime
from urllib.parse import urlsplit

import httpx

from scholar.domain.interfaces.paper_enricher import PaperEnrichment
from scholar.domain.interfaces.provider_error import (
    ProviderError,
    provider_error_message,
)
from scholar.domain.models.fields import AcademicField
from scholar.domain.models.identifiers import (
    IdentifierKind,
    PaperIdentifier,
    normalize_arxiv,
    normalize_doi,
    parse_identifier,
)
from scholar.domain.models.paper import Paper, PaperSource
from scholar.providers.field_mapping import OPENALEX_FILTERS

_FIELDS = (
    "id,doi,title,abstract_inverted_index,authorships,publication_date,publication_year,"
    "primary_location,best_oa_location,locations,updated_date,ids,cited_by_count,fwci,"
    "citation_normalized_percentile,primary_topic,open_access,is_retracted,mesh,study_designs"
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
    ) -> list[Paper]:
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
        return [self.to_paper(work) for work in response.json()["results"]]

    async def enrich(
        self,
        identifiers: list[PaperIdentifier],
        *,
        include_details: bool = False,
        api_key: str | None = None,
    ) -> list[PaperEnrichment | None]:
        """精确读取 Work；单篇接口保留完整作者，返回顺序与输入一一对应。"""
        if not api_key:
            raise ProviderError("OpenAlex API key is required")

        async def resolve(identifier):
            async with self._slots:
                paper = await self.lookup(identifier, api_key=api_key)
            if paper is None:
                return None
            aliases = [identifier, parse_identifier(paper.metadata["openalex"]["id"])]
            if paper.arxiv_id is not None:
                aliases.append(parse_identifier(paper.arxiv_id))
            if paper.doi is not None:
                aliases.append(parse_identifier(paper.doi))
            return PaperEnrichment(
                list(dict.fromkeys(aliases)),
                paper.metadata,
                paper if include_details else None,
            )

        tasks = [asyncio.create_task(resolve(identifier)) for identifier in identifiers]
        try:
            return list(await asyncio.gather(*tasks))
        finally:
            # 一个请求失败或调用者取消时，批次的其余 HTTP 不得游离在用例生命周期之外。
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def lookup(
        self, identifier: PaperIdentifier, *, api_key: str
    ) -> Paper | None:
        if not api_key:
            raise ProviderError("OpenAlex API key is required")
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
        return self.to_paper(response.json())

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
        arxiv_ids = set()
        locations = work["locations"]
        for location in locations:
            landing = location["landing_page_url"]
            if landing and urlsplit(landing).hostname in (
                "arxiv.org",
                "www.arxiv.org",
                "export.arxiv.org",
            ):
                arxiv_ids.add(normalize_arxiv(landing))
        if doi and doi.startswith("10.48550/arxiv."):
            arxiv_ids.add(normalize_arxiv(doi.removeprefix("10.48550/arxiv.")))
        # 上游一个 work 若包含不同 arXiv 身份，不任意选一个用于跨源合并。
        arxiv_id = next(iter(arxiv_ids)) if len(arxiv_ids) == 1 else None
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
                "fwci",
                "citation_normalized_percentile",
                "primary_topic",
                "open_access",
                "is_retracted",
                "mesh",
                "study_designs",
            )
        }
        values.update(
            {
                "observed_at": datetime.now(UTC).isoformat(),
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
                "authorships": [
                    {
                        "author": {
                            key: entry["author"].get(key)
                            for key in ("id", "display_name", "orcid")
                        },
                        "author_position": entry.get("author_position"),
                        "is_corresponding": entry.get("is_corresponding"),
                        "institutions": [
                            {
                                key: institution.get(key)
                                for key in (
                                    "id",
                                    "display_name",
                                    "ror",
                                    "country_code",
                                    "type",
                                )
                            }
                            for institution in entry.get("institutions", [])
                        ],
                    }
                    for entry in work["authorships"]
                ],
            }
        )
        return values
