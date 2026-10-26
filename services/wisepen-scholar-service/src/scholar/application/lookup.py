"""精确论文详情查询；保留输入顺序、歧义及逐项失败语义。"""

from dataclasses import dataclass
from enum import StrEnum

from common.core.exceptions import ServiceException

from scholar.application.source_execution import SourceExecution, SourceExecutionStatus
from scholar.domain.error_codes import ScholarErrorCode
from scholar.domain.models.identifiers import IdentifierKind, parse_identifier
from scholar.domain.models.paper import Paper, PaperSource
from scholar.domain.repositories.paper_repository import (
    LookupFailureReason,
    PaperRepository,
)


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
    failure_reason: LookupFailureReason | None = None


@dataclass
class LookupResult:
    items: list[LookupItem]
    execution: list[SourceExecution]


class PaperLookupService:
    def __init__(self, paper_repository: PaperRepository):
        self.paper_repository = paper_repository

    async def lookup(self, query: PaperLookup) -> LookupResult:
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
            if normalized.kind not in (IdentifierKind.ARXIV, IdentifierKind.DOI):
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
        found = await self.paper_repository.lookup(list(parsed))
        failed = 0
        for positions, match in zip(parsed.values(), found, strict=True):
            if match.failure_reason is not None:
                failed += 1
                for position in positions:
                    items[position] = LookupItem(
                        identifier=items[position].identifier,
                        status=LookupStatus.FAILED,
                        failure_reason=match.failure_reason,
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
        status = (
            SourceExecutionStatus.COMPLETED
            if not failed
            else SourceExecutionStatus.FAILED
            if failed == len(found)
            else SourceExecutionStatus.PARTIAL
        )
        return LookupResult(
            items=items,
            execution=[
                SourceExecution(
                    source=PaperSource.ARXIV,
                    cache_hit=False,
                    coverage="本地 arXiv 精确身份查询。",
                    status=status,
                )
            ]
            if parsed
            else [],
        )
