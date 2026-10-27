"""精确论文详情查询；保留输入顺序、歧义及逐项失败语义。"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum

from common.core.exceptions import ServiceException

from scholar.application.source_execution import SourceExecution, SourceExecutionStatus
from scholar.domain.error_codes import ScholarErrorCode
from scholar.domain.interfaces.provider_error import ProviderError
from scholar.domain.models.identifiers import (
    IdentifierKind,
    parse_identifier,
)
from scholar.domain.models.paper import Paper, PaperSource
from scholar.domain.repositories.paper_repository import PaperRepository


class LookupPurpose(StrEnum):
    DETAILS = "details"
    ENRICH = "enrich"
    FULL = "full"


class LookupStatus(StrEnum):
    FOUND = "found"
    NOT_FOUND = "not_found"
    INVALID_IDENTIFIER = "invalid_identifier"
    UNSUPPORTED_IDENTIFIER = "unsupported_identifier"
    AMBIGUOUS = "ambiguous"
    MISSING_API_KEY = "missing_api_key"
    FAILED = "failed"


@dataclass
class PaperLookup:
    identifiers: list[str]
    purpose: LookupPurpose = LookupPurpose.DETAILS


@dataclass
class LookupItem:
    identifier: str
    status: LookupStatus
    paper: Paper | None = None
    error: str | None = None


@dataclass
class LookupResult:
    items: list[LookupItem]
    execution: list[SourceExecution]


class PaperLookupService:
    def __init__(
        self,
        paper_repository: PaperRepository,
        openalex_lookup: Callable[..., Awaitable[Paper | None]] | None = None,
    ):
        self.paper_repository = paper_repository
        self.openalex_lookup = openalex_lookup

    async def lookup(
        self, query: PaperLookup, *, api_key: str | None = None
    ) -> LookupResult:
        if query.purpose != LookupPurpose.DETAILS:
            raise ServiceException(ScholarErrorCode.CAPABILITY_UNAVAILABLE)
        items, parsed = [], {}
        for identifier in query.identifiers:
            try:
                normalized = parse_identifier(identifier)
            except ValueError:
                items.append(
                    LookupItem(
                        identifier=identifier, status=LookupStatus.INVALID_IDENTIFIER
                    )
                )
                continue
            if normalized.kind not in (
                IdentifierKind.ARXIV,
                IdentifierKind.DOI,
                IdentifierKind.OPENALEX,
            ):
                items.append(
                    LookupItem(
                        identifier=identifier,
                        status=LookupStatus.UNSUPPORTED_IDENTIFIER,
                    )
                )
                continue
            parsed.setdefault(normalized, []).append(len(items))
            items.append(
                LookupItem(identifier=identifier, status=LookupStatus.NOT_FOUND)
            )
        # OpenAlex 身份没有本地 arXiv/DOI 字段可查，不把它误投到 DOI 查询。
        local = {
            identifier: positions
            for identifier, positions in parsed.items()
            if identifier.kind in (IdentifierKind.ARXIV, IdentifierKind.DOI)
        }
        local_failures = 0
        local_error = None
        found = await self.paper_repository.lookup(list(local)) if local else []
        for positions, match in zip(local.values(), found, strict=True):
            if match.error is not None:
                local_failures += 1
                local_error = local_error or match.error
                for position in positions:
                    items[position] = LookupItem(
                        identifier=items[position].identifier,
                        status=LookupStatus.FAILED,
                        error=match.error,
                    )
                continue
            papers = match.papers
            status = (
                LookupStatus.FOUND
                if len(papers) == 1
                else LookupStatus.AMBIGUOUS
                if papers
                else LookupStatus.NOT_FOUND
            )
            paper = papers[0] if status == LookupStatus.FOUND else None
            for position in positions:
                items[position] = LookupItem(
                    identifier=items[position].identifier, status=status, paper=paper
                )

        openalex_attempts = 0
        openalex_failures = 0
        missing_api_keys = 0
        openalex_error = None
        first_provider_error = None
        for identifier, positions in parsed.items():
            if items[positions[0]].status != LookupStatus.NOT_FOUND:
                continue
            if identifier.kind == IdentifierKind.OPENALEX and api_key is None:
                missing_api_keys += 1
                for position in positions:
                    items[position] = LookupItem(
                        identifier=items[position].identifier,
                        status=LookupStatus.MISSING_API_KEY,
                    )
                continue
            if api_key is None:
                continue
            if self.openalex_lookup is None:
                raise ServiceException(ScholarErrorCode.CAPABILITY_UNAVAILABLE)

            openalex_attempts += 1
            try:
                paper = await self.openalex_lookup(identifier, api_key=api_key)
            except ProviderError as error:
                openalex_failures += 1
                if openalex_error is None:
                    openalex_error = str(error)
                    first_provider_error = error
                for position in positions:
                    items[position] = LookupItem(
                        identifier=items[position].identifier,
                        status=LookupStatus.FAILED,
                        error=str(error),
                    )
                continue

            status = LookupStatus.FOUND if paper is not None else LookupStatus.NOT_FOUND
            for position in positions:
                items[position] = LookupItem(
                    identifier=items[position].identifier,
                    status=status,
                    paper=paper,
                )

        has_usable_result = any(
            item.status
            in (
                LookupStatus.FOUND,
                LookupStatus.NOT_FOUND,
                LookupStatus.AMBIGUOUS,
                LookupStatus.MISSING_API_KEY,
            )
            for item in items
        )
        if not has_usable_result:
            if first_provider_error is not None:
                raise first_provider_error
            if local_error is not None:
                raise ProviderError(local_error)

        execution = []
        if local:
            local_status = (
                SourceExecutionStatus.COMPLETED
                if not local_failures
                else SourceExecutionStatus.FAILED
                if local_failures == len(local)
                else SourceExecutionStatus.PARTIAL
            )
            execution.append(
                SourceExecution(
                    source=PaperSource.ARXIV,
                    cache_hit=False,
                    coverage="本地精确身份查询。",
                    status=local_status,
                    error=local_error,
                )
            )
        if openalex_attempts or missing_api_keys:
            total_openalex = openalex_attempts + missing_api_keys
            openalex_status = (
                SourceExecutionStatus.COMPLETED
                if not openalex_failures and not missing_api_keys
                else SourceExecutionStatus.FAILED
                if openalex_failures + missing_api_keys == total_openalex
                else SourceExecutionStatus.PARTIAL
            )
            execution.append(
                SourceExecution(
                    source=PaperSource.OPENALEX,
                    cache_hit=False,
                    coverage="按精确标识查询 OpenAlex。",
                    status=openalex_status,
                    error=openalex_error,
                )
            )
        return LookupResult(
            items=items,
            execution=execution,
        )
