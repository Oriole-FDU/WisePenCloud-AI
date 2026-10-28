"""arXiv 在线搜索适配；领域到分类的转换统一由 field_mapping 负责。"""

from __future__ import annotations

from datetime import date

from elastic_transport import (
    ApiError as ElasticApiError,
)
from elastic_transport import (
    ConnectionError as ElasticConnectionError,
)
from elastic_transport import (
    ConnectionTimeout as ElasticConnectionTimeout,
)

from scholar.domain.interfaces.paper_enricher import PaperEnrichment
from scholar.domain.interfaces.paper_searcher import PaperSearchBatch
from scholar.domain.interfaces.provider_error import (
    ProviderError,
    provider_error_message,
)
from scholar.domain.models.fields import AcademicField
from scholar.domain.models.identifiers import PaperIdentifier, parse_identifier
from scholar.domain.repositories.paper_repository import PaperRepository
from scholar.providers.field_mapping import ARXIV_CATEGORIES


class ArxivProvider:
    def __init__(self, paper_repository: PaperRepository):
        self.paper_repository = paper_repository

    async def enrich(
        self,
        identifiers: list[PaperIdentifier],
        *,
        include_details: bool = False,
        api_key: str | None = None,
    ) -> list[PaperEnrichment | None]:
        """按 arXiv/DOI 精确身份批量投影仓储已有事实，不触发外部请求。"""
        try:
            matches = await self.paper_repository.lookup(identifiers)
        except ElasticApiError as error:
            raise ProviderError(provider_error_message(error)) from error
        except (ElasticConnectionError, ElasticConnectionTimeout) as error:
            raise ProviderError(provider_error_message(error)) from error

        enrichments = []
        for identifier, match in zip(identifiers, matches, strict=True):
            if match.error is not None:
                raise ProviderError(match.error)
            if len(match.papers) != 1:
                enrichments.append(None)
                continue

            paper = match.papers[0]
            aliases = [identifier]
            if paper.arxiv_id is not None:
                aliases.append(parse_identifier(paper.arxiv_id))
            if paper.doi is not None:
                aliases.append(parse_identifier(paper.doi))
            enrichments.append(
                PaperEnrichment(
                    identifiers=list(dict.fromkeys(aliases)),
                    metadata=paper.metadata,
                    paper=paper if include_details else None,
                )
            )
        return enrichments

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
        categories = ARXIV_CATEGORIES[field]
        if categories is None:
            raise ValueError(f"arXiv search is not mapped for {field.value}")

        try:
            papers = await self.paper_repository.search(
                query=query,
                categories=categories,
                published_after=published_after,
                published_before=published_before,
                limit=limit,
            )
            return PaperSearchBatch(papers)
        except ElasticApiError as error:
            raise ProviderError(provider_error_message(error)) from error
        except (ElasticConnectionError, ElasticConnectionTimeout) as error:
            raise ProviderError(provider_error_message(error)) from error
