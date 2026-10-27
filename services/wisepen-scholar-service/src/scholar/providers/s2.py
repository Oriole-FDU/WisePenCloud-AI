"""Semantic Scholar 论文富化适配器。"""

from __future__ import annotations

from datetime import UTC, date, datetime

import httpx
from redis.exceptions import RedisError

from scholar.core.s2_transport import S2Transport
from scholar.domain.interfaces.paper_enricher import PaperEnrichment
from scholar.domain.interfaces.provider_error import (
    ProviderError,
    provider_error_message,
)
from scholar.domain.models.identifiers import (
    IdentifierKind,
    PaperIdentifier,
    normalize_arxiv,
    normalize_doi,
    parse_identifier,
)
from scholar.domain.models.paper import Paper, PaperSource

IDENTITY_FIELDS = ("paperId", "corpusId", "externalIds", "url", "title")
ENRICHMENT_FIELDS = (
    "tldr",
    "citationCount",
    "influentialCitationCount",
    "isOpenAccess",
    "openAccessPdf",
    "citationStyles",
)
DETAIL_FIELDS = ("abstract", "authors", "year", "publicationDate", "venue")


class S2Provider:
    def __init__(self, transport: S2Transport):
        self.transport = transport

    async def enrich(
        self,
        identifiers: list[PaperIdentifier],
        *,
        include_details: bool = False,
        api_key: str | None = None,
    ) -> list[PaperEnrichment | None]:
        fields = IDENTITY_FIELDS + ENRICHMENT_FIELDS
        if include_details:
            fields += DETAIL_FIELDS
        try:
            response = await self.transport.request(
                "POST",
                "graph/v1/paper/batch",
                params={"fields": ",".join(fields)},
                json={
                    "ids": [self._serialize(identifier) for identifier in identifiers]
                },
            )
        except (httpx.HTTPError, RedisError) as error:
            raise ProviderError(provider_error_message(error)) from error
        return [
            None if item is None else self._project(item, include_details)
            for item in response
        ]

    @staticmethod
    def _serialize(identifier: PaperIdentifier) -> str:
        if identifier.kind == IdentifierKind.ARXIV:
            return f"arxiv:{identifier.value}"
        if identifier.kind == IdentifierKind.DOI:
            return f"doi:{identifier.value}"
        if identifier.kind == IdentifierKind.CORPUS_ID:
            return f"CorpusId:{identifier.value}"
        if identifier.kind == IdentifierKind.S2:
            return identifier.value
        raise ValueError(f"S2 does not accept {identifier.kind.value} identifiers")

    @classmethod
    def _project(cls, payload: dict, include_details: bool) -> PaperEnrichment:
        external_ids = payload.get("externalIds") or {}
        tldr = payload.get("tldr")
        pdf = payload.get("openAccessPdf")
        citation_styles = payload.get("citationStyles")
        metadata = {
            "s2": {
                "corpus_id": None
                if payload.get("corpusId") is None
                else str(payload["corpusId"]),
                "paper_id": payload.get("paperId"),
                "citation_count": payload.get("citationCount"),
                "influential_citation_count": payload.get("influentialCitationCount"),
                "is_open_access": payload.get("isOpenAccess"),
                "tldr": None
                if tldr is None
                else {"model": tldr.get("model"), "text": tldr.get("text")},
                "open_access_pdf": None if pdf is None else pdf.get("url") or None,
                "bibtex": None
                if citation_styles is None
                else citation_styles.get("bibtex"),
                "observed_at": datetime.now(UTC).isoformat(),
            }
        }
        if include_details:
            metadata["s2"]["venue"] = payload.get("venue")
        actual_ids = cls._identifiers(payload)
        paper = cls._paper(payload, external_ids, metadata) if include_details else None
        return PaperEnrichment(identifiers=actual_ids, metadata=metadata, paper=paper)

    @staticmethod
    def _identifiers(payload: dict) -> list[PaperIdentifier]:
        identifiers = []
        if payload.get("paperId"):
            identifiers.append(parse_identifier(f"s2:{payload['paperId']}"))
        if payload.get("corpusId") is not None:
            identifiers.append(parse_identifier(f"corpusid:{payload['corpusId']}"))
        external_ids = payload.get("externalIds") or {}
        if external_ids.get("ArXiv"):
            identifiers.append(parse_identifier(external_ids["ArXiv"]))
        if external_ids.get("DOI"):
            identifiers.append(parse_identifier(external_ids["DOI"]))
        return list(dict.fromkeys(identifiers))

    @staticmethod
    def _paper(payload: dict, external_ids: dict, metadata: dict) -> Paper:
        publication_date = payload.get("publicationDate")
        authors = payload.get("authors")
        return Paper(
            arxiv_id=None
            if not external_ids.get("ArXiv")
            else normalize_arxiv(external_ids["ArXiv"]),
            doi=None
            if not external_ids.get("DOI")
            else normalize_doi(external_ids["DOI"]),
            title=payload.get("title"),
            abstract=payload.get("abstract"),
            authors=[] if authors is None else [author["name"] for author in authors],
            publication_date=None
            if publication_date is None
            else date.fromisoformat(publication_date),
            year=payload.get("year"),
            url=payload["url"],
            pdf_url=metadata["s2"]["open_access_pdf"],
            source=PaperSource.S2,
            metadata=metadata,
        )
