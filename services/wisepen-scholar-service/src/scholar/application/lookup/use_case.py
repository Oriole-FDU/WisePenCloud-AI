"""精确论文查询；统一管理本地事实、OpenAlex 回填和 S2 富化。"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from common.core.exceptions import ServiceException

from scholar.application.lookup.enrichment import (
    NEGATIVE_TTL_SECONDS,
    POSITIVE_TTL_SECONDS,
    EnrichmentStatus,
    EnrichmentTarget,
    _S2Enrichment,
)
from scholar.application.source_execution import SourceExecution, SourceExecutionStatus
from scholar.domain.error_codes import ScholarErrorCode
from scholar.domain.interfaces.paper_enricher import PaperEnricher
from scholar.domain.interfaces.provider_error import ProviderError
from scholar.domain.models.identifiers import (
    IdentifierKind,
    PaperIdentifier,
    parse_identifier,
)
from scholar.domain.models.paper import Paper, PaperSource
from scholar.domain.repositories.paper_enrichment_cache import PaperEnrichmentCache
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
    metadata: dict[str, Any] | None = None
    enrichment_status: EnrichmentStatus | None = None
    error: str | None = None


@dataclass
class LookupResult:
    items: list[LookupItem]
    execution: list[SourceExecution]


class PaperLookupService:
    """按输入位置返回 lookup 结果，并在同一用例内协调缓存和外部来源。"""

    def __init__(
        self,
        paper_repository: PaperRepository,
        openalex_lookup: Callable[..., Awaitable[Paper | None]] | None = None,
        *,
        s2_enricher: PaperEnricher | None = None,
        s2_cache: PaperEnrichmentCache | None = None,
        positive_ttl_seconds: int = POSITIVE_TTL_SECONDS,
        negative_ttl_seconds: int = NEGATIVE_TTL_SECONDS,
    ):
        self.paper_repository = paper_repository
        self.openalex_lookup = openalex_lookup
        self._s2 = _S2Enrichment(
            s2_enricher,
            s2_cache,
            positive_ttl_seconds=positive_ttl_seconds,
            negative_ttl_seconds=negative_ttl_seconds,
        )

    async def lookup(
        self, query: PaperLookup, *, api_key: str | None = None
    ) -> LookupResult:
        items: list[LookupItem] = []
        positions: dict[PaperIdentifier, list[int]] = {}
        local_ids: dict[PaperIdentifier, list[int]] = {}
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

            if (
                normalized.kind == IdentifierKind.OPENALEX
                and query.purpose == LookupPurpose.ENRICH
            ):
                items.append(
                    LookupItem(
                        identifier=identifier,
                        status=LookupStatus.UNSUPPORTED_IDENTIFIER,
                    )
                )
                continue
            accepted = {
                IdentifierKind.ARXIV,
                IdentifierKind.DOI,
                IdentifierKind.OPENALEX,
                IdentifierKind.S2,
                IdentifierKind.CORPUS_ID,
            }
            if normalized.kind not in accepted:
                items.append(
                    LookupItem(
                        identifier=identifier,
                        status=LookupStatus.UNSUPPORTED_IDENTIFIER,
                    )
                )
                continue

            index = len(items)
            positions.setdefault(normalized, []).append(index)
            if normalized.kind in (IdentifierKind.ARXIV, IdentifierKind.DOI):
                local_ids[normalized] = positions[normalized]
            items.append(
                LookupItem(identifier=identifier, status=LookupStatus.NOT_FOUND)
            )

        execution: list[SourceExecution] = []
        local_failures: set[PaperIdentifier] = set()
        local_error = None
        if local_ids:
            matches = await self.paper_repository.lookup(list(local_ids))
            for identifier, match in zip(local_ids, matches, strict=True):
                same_identity = local_ids[identifier]
                if match.error is not None:
                    local_failures.add(identifier)
                    local_error = local_error or match.error
                    for index in same_identity:
                        items[index].status = LookupStatus.FAILED
                        items[index].error = match.error
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
                for index in same_identity:
                    items[index].status = status
                    items[index].paper = paper

            failed_count = len(local_failures)
            execution.append(
                SourceExecution(
                    source=PaperSource.ARXIV,
                    cache_hit=False,
                    coverage="本地精确身份查询。",
                    status=(
                        SourceExecutionStatus.COMPLETED
                        if not failed_count
                        else SourceExecutionStatus.FAILED
                        if failed_count == len(local_ids)
                        else SourceExecutionStatus.PARTIAL
                    ),
                    error=local_error,
                )
            )

        openalex_attempts = 0
        openalex_failures = 0
        missing_api_keys = 0
        openalex_error = None
        first_provider_error: Exception | None = None
        if query.purpose != LookupPurpose.ENRICH:
            for identifier, same_identity in positions.items():
                if identifier.kind not in (
                    IdentifierKind.ARXIV,
                    IdentifierKind.DOI,
                    IdentifierKind.OPENALEX,
                ):
                    continue
                if items[same_identity[0]].status != LookupStatus.NOT_FOUND:
                    continue
                if identifier.kind == IdentifierKind.OPENALEX and api_key is None:
                    missing_api_keys += 1
                    for index in same_identity:
                        items[index].status = LookupStatus.MISSING_API_KEY
                    continue
                if api_key is None:
                    continue
                if self.openalex_lookup is None:
                    if (
                        query.purpose == LookupPurpose.DETAILS
                        or identifier.kind == IdentifierKind.OPENALEX
                    ):
                        raise ServiceException(ScholarErrorCode.CAPABILITY_UNAVAILABLE)
                    continue

                openalex_attempts += 1
                try:
                    paper = await self.openalex_lookup(identifier, api_key=api_key)
                except ProviderError as error:
                    openalex_failures += 1
                    openalex_error = openalex_error or str(error)
                    first_provider_error = first_provider_error or error
                    for index in same_identity:
                        items[index].status = LookupStatus.FAILED
                        items[index].error = str(error)
                    continue
                status = (
                    LookupStatus.FOUND if paper is not None else LookupStatus.NOT_FOUND
                )
                for index in same_identity:
                    items[index].status = status
                    items[index].paper = paper

        if openalex_attempts or missing_api_keys:
            total = openalex_attempts + missing_api_keys
            execution.append(
                SourceExecution(
                    source=PaperSource.OPENALEX,
                    cache_hit=False,
                    coverage="按精确标识查询 OpenAlex。",
                    status=(
                        SourceExecutionStatus.COMPLETED
                        if not openalex_failures and not missing_api_keys
                        else SourceExecutionStatus.FAILED
                        if openalex_failures + missing_api_keys == total
                        else SourceExecutionStatus.PARTIAL
                    ),
                    error=openalex_error,
                )
            )

        targets: list[EnrichmentTarget] = []
        target_positions: dict[PaperIdentifier, list[int]] = {}
        if query.purpose == LookupPurpose.DETAILS:
            for identifier, same_identity in positions.items():
                if identifier.kind in (IdentifierKind.S2, IdentifierKind.CORPUS_ID):
                    target_positions[identifier] = same_identity
                    targets.append(EnrichmentTarget(identifier, None))
                elif identifier.kind in (IdentifierKind.ARXIV, IdentifierKind.DOI):
                    if (
                        identifier in local_failures
                        or items[same_identity[0]].status == LookupStatus.AMBIGUOUS
                    ):
                        continue
                    paper = items[same_identity[0]].paper
                    cache_id = identifier
                    target_positions.setdefault(cache_id, []).extend(same_identity)
                    targets.append(EnrichmentTarget(cache_id, paper))
                elif identifier.kind == IdentifierKind.OPENALEX:
                    paper = items[same_identity[0]].paper
                    cache_id = self._paper_identity(paper)
                    if cache_id is not None:
                        target_positions.setdefault(cache_id, []).extend(same_identity)
                        targets.append(EnrichmentTarget(cache_id, paper))
        else:
            for identifier, same_identity in positions.items():
                status = items[same_identity[0]].status
                if identifier.kind in (IdentifierKind.S2, IdentifierKind.CORPUS_ID):
                    target_id = identifier
                    paper = None
                elif identifier.kind in (IdentifierKind.ARXIV, IdentifierKind.DOI):
                    if identifier in local_failures or status == LookupStatus.AMBIGUOUS:
                        continue
                    paper = items[same_identity[0]].paper
                    target_id = identifier
                elif identifier.kind == IdentifierKind.OPENALEX:
                    if (
                        query.purpose != LookupPurpose.FULL
                        or status != LookupStatus.FOUND
                    ):
                        continue
                    paper = items[same_identity[0]].paper
                    target_id = self._paper_identity(paper)
                    if target_id is None:
                        for index in same_identity:
                            items[
                                index
                            ].enrichment_status = EnrichmentStatus.UNSUPPORTED
                        continue
                else:
                    continue
                target_positions.setdefault(target_id, []).extend(same_identity)
                targets.append(EnrichmentTarget(target_id, paper))

        first_enrichment_error: Exception | None = None
        if targets:
            outcomes, s2_execution = await self._s2.resolve(
                targets,
                full=query.purpose == LookupPurpose.FULL,
                cache_only=query.purpose == LookupPurpose.DETAILS,
            )
            execution.append(s2_execution)
            for target_id, same_identity in target_positions.items():
                outcome = outcomes[target_id]
                if outcome.error is not None:
                    first_enrichment_error = first_enrichment_error or outcome.error
                for index in same_identity:
                    item = items[index]
                    had_base = item.paper is not None
                    if outcome.paper is not None:
                        item.paper = (
                            None
                            if query.purpose == LookupPurpose.ENRICH
                            else outcome.paper
                        )
                    if outcome.metadata is not None:
                        item.metadata = outcome.metadata
                    if query.purpose != LookupPurpose.DETAILS:
                        item.enrichment_status = outcome.status
                    if outcome.error is not None:
                        item.error = str(outcome.error)

                    if query.purpose == LookupPurpose.DETAILS:
                        if outcome.error is not None:
                            item.status = (
                                LookupStatus.FOUND
                                if item.paper is not None
                                else LookupStatus.FAILED
                            )
                        elif item.paper is not None:
                            item.status = LookupStatus.FOUND
                            if outcome.cache_hit:
                                item.error = None
                        elif outcome.cache_hit and item.status != LookupStatus.FAILED:
                            item.status = LookupStatus.NOT_FOUND
                        elif (
                            item.status != LookupStatus.FAILED
                            and outcome.status == EnrichmentStatus.COMPLETE
                        ):
                            item.status = LookupStatus.FOUND
                        elif item.status != LookupStatus.FAILED:
                            item.status = LookupStatus.NOT_FOUND
                    elif (
                        query.purpose == LookupPurpose.ENRICH
                        and (
                            item.metadata is not None
                            or outcome.status == EnrichmentStatus.COMPLETE
                        )
                    ) or (
                        query.purpose != LookupPurpose.ENRICH
                        and (
                            had_base
                            or item.paper is not None
                            or outcome.status == EnrichmentStatus.COMPLETE
                        )
                    ):
                        item.status = LookupStatus.FOUND
                    elif outcome.status == EnrichmentStatus.NOT_FOUND:
                        item.status = LookupStatus.NOT_FOUND
                    elif outcome.status == EnrichmentStatus.UNSUPPORTED:
                        item.status = LookupStatus.UNSUPPORTED_IDENTIFIER
                    else:
                        item.status = LookupStatus.FAILED

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
            if first_enrichment_error is not None:
                raise first_enrichment_error
            if local_error is not None:
                raise ProviderError(local_error)

        return LookupResult(items=items, execution=execution)

    @staticmethod
    def _paper_identity(paper: Paper | None) -> PaperIdentifier | None:
        if paper is None:
            return None
        if paper.arxiv_id:
            return parse_identifier(paper.arxiv_id)
        if paper.doi:
            return parse_identifier(paper.doi)
        return None
