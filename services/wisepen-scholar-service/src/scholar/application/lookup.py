"""精确 lookup 用例；details 读取基础事实，full/enrich 执行领域声明的富化。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from copy import deepcopy
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from common.core.exceptions import ServiceException

from scholar.application.enrichment import EnrichmentRunner
from scholar.application.field_policies import (
    COMMON_ENRICHERS,
    FIELD_POLICIES,
    FieldPolicy,
)
from scholar.application.source_execution import SourceExecution, SourceExecutionStatus
from scholar.domain.error_codes import ScholarErrorCode
from scholar.domain.interfaces.provider_error import ProviderError
from scholar.domain.models.fields import AcademicField
from scholar.domain.models.identifiers import (
    IdentifierKind,
    PaperIdentifier,
    parse_identifier,
)
from scholar.domain.models.paper import Paper, PaperSource
from scholar.domain.repositories.paper_repository import (
    PaperLookupMatch,
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
    MISSING_API_KEY = "missing_api_key"
    FAILED = "failed"


class EnrichmentStatus(StrEnum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    NOT_FOUND = "not_found"
    FAILED = "failed"
    UNSUPPORTED = "unsupported"


@dataclass
class PaperLookup:
    identifiers: list[str]
    purpose: LookupPurpose = LookupPurpose.DETAILS
    field: AcademicField = AcademicField.GENERAL


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
    """保留输入位置与身份歧义；富化只依赖注册能力，不识别特定供应商字段。"""

    def __init__(
        self,
        paper_repository: PaperRepository,
        openalex_lookup: Callable[..., Awaitable[Paper | None]] | None = None,
        *,
        enrichment: EnrichmentRunner | None = None,
        policies: dict[AcademicField, FieldPolicy] | None = None,
        platform_credentials: dict[str, str] | None = None,
    ):
        self.platform_credentials = platform_credentials or {}
        self.paper_repository = paper_repository
        self.openalex_lookup = openalex_lookup
        self.enrichment = enrichment if enrichment is not None else EnrichmentRunner({})
        self.policies = FIELD_POLICIES if policies is None else policies

    async def lookup(
        self,
        query: PaperLookup,
        *,
        api_key: str | None = None,
        credentials: dict[str, str] | None = None,
    ) -> LookupResult:
        credentials = self.platform_credentials | (credentials or {})
        if api_key:
            credentials[PaperSource.OPENALEX] = api_key
        api_key = credentials.get(PaperSource.OPENALEX)
        items = []
        positions: dict[PaperIdentifier, list[int]] = {}
        for value in query.identifiers:
            try:
                identifier = parse_identifier(value)
            except ValueError:
                items.append(LookupItem(value, LookupStatus.INVALID_IDENTIFIER))
                continue
            positions.setdefault(identifier, []).append(len(items))
            items.append(LookupItem(value, LookupStatus.NOT_FOUND))

        local_ids = [
            identity
            for identity in positions
            if identity.kind in (IdentifierKind.ARXIV, IdentifierKind.DOI)
        ]
        execution = []
        first_error: Exception | None = None
        local_errors = {}
        if local_ids:
            try:
                matches = await self.paper_repository.lookup(local_ids)
            except ProviderError as error:
                if query.purpose == LookupPurpose.DETAILS:
                    raise
                # 通用链允许本地来源失败后继续；仍保留原始仓储异常供全失败时抛出。
                matches = [PaperLookupMatch([], str(error)) for _ in local_ids]
                local_errors = dict.fromkeys(local_ids, error)
                first_error = error
            failures = []
            for identity, match in zip(local_ids, matches, strict=True):
                if match.error is not None:
                    error = local_errors.get(identity) or ProviderError(match.error)
                    first_error = first_error or error
                    failures.append(error)
                    local_errors[identity] = error
                    status, paper = LookupStatus.FAILED, None
                else:
                    status = (
                        LookupStatus.FOUND
                        if len(match.papers) == 1
                        else LookupStatus.AMBIGUOUS
                        if match.papers
                        else LookupStatus.NOT_FOUND
                    )
                    paper = match.papers[0] if status == LookupStatus.FOUND else None
                for index in positions[identity]:
                    items[index].status, items[index].paper = status, deepcopy(paper)
                    items[index].error = match.error
            execution.append(
                SourceExecution(
                    PaperSource.ARXIV,
                    False,
                    SourceExecutionStatus.FAILED
                    if len(failures) == len(local_ids)
                    else SourceExecutionStatus.PARTIAL
                    if failures
                    else SourceExecutionStatus.COMPLETED,
                    str(failures[0]) if failures else None,
                )
            )

        if query.purpose == LookupPurpose.DETAILS:
            detail_execution, detail_error = await self._details(
                items, positions, api_key
            )
            execution.extend(detail_execution)
            first_error = first_error or detail_error

        targets = []
        target_positions = []
        for identity, indexes in positions.items():
            item = items[indexes[0]]
            if item.status in (
                LookupStatus.AMBIGUOUS,
                LookupStatus.MISSING_API_KEY,
            ):
                continue
            if (
                item.status == LookupStatus.FAILED
                and query.purpose == LookupPurpose.DETAILS
            ):
                continue
            target = self.enrichment.target(identity, item.paper)
            if identity in local_errors:
                target.errors.append(local_errors[identity])
            if identity in local_ids:
                # 复用本次已经查询的仓储结果，包括确认未找到，避免再读一次本地库。
                target.observed_sources.add(PaperSource.ARXIV)
            targets.append(target)
            target_positions.append(indexes)

        if query.purpose == LookupPurpose.DETAILS:
            cached_sources = [
                source
                for source, binding in self.enrichment.bindings.items()
                if binding.cache is not None
            ]
            execution.extend(
                await self.enrichment.run(
                    targets,
                    common_sources=[],
                    domain_sources=cached_sources,
                    full=True,
                    cache_only=True,
                    credentials=credentials,
                )
            )
        else:
            policy = self.policies[query.field]
            execution.extend(
                await self.enrichment.run(
                    targets,
                    common_sources=[
                        source
                        for source in COMMON_ENRICHERS
                        if source in self.enrichment.bindings
                    ],
                    domain_sources=policy.field_enrichers,
                    credentials=credentials,
                    full=query.purpose == LookupPurpose.FULL,
                )
            )

        for target, indexes in zip(targets, target_positions, strict=True):
            if target.errors:
                first_error = first_error or target.errors[0]
            for index in indexes:
                item = items[index]
                item.paper = (
                    deepcopy(target.paper)
                    if query.purpose != LookupPurpose.ENRICH
                    else None
                )
                item.metadata = deepcopy(target.metadata) or None
                if query.purpose == LookupPurpose.DETAILS:
                    if item.paper is not None:
                        item.status = LookupStatus.FOUND
                    elif target.errors:
                        item.status = LookupStatus.FAILED
                    if target.errors:
                        item.error = str(target.errors[0])
                    continue
                if target.has_signals:
                    partial = not target.general_found or bool(target.domain_errors)
                    item.enrichment_status = (
                        EnrichmentStatus.PARTIAL
                        if partial
                        else EnrichmentStatus.COMPLETE
                    )
                    item.status = (
                        LookupStatus.FOUND
                        if query.purpose == LookupPurpose.ENRICH
                        or item.paper is not None
                        else LookupStatus.NOT_FOUND
                    )
                    # 通用回退成功已补足前面的失败；只把仍影响结果的错误放到论文项上。
                    item.error = (
                        str(target.domain_errors[0])
                        if target.domain_errors
                        else str(target.errors[0])
                        if not target.general_found and target.errors
                        else None
                    )
                elif target.errors:
                    item.enrichment_status = (
                        EnrichmentStatus.PARTIAL
                        if item.paper is not None
                        else EnrichmentStatus.FAILED
                    )
                    item.status = (
                        LookupStatus.FOUND
                        if item.paper is not None
                        else LookupStatus.FAILED
                    )
                    item.error = str(target.errors[0])
                else:
                    supported = any(
                        identity.kind in binding.identifier_kinds
                        for identity in target.identifiers
                        for binding in self.enrichment.bindings.values()
                    )
                    item.enrichment_status = (
                        EnrichmentStatus.PARTIAL
                        if item.paper is not None
                        else EnrichmentStatus.NOT_FOUND
                        if supported
                        else EnrichmentStatus.UNSUPPORTED
                    )
                    item.status = (
                        LookupStatus.FOUND
                        if item.paper is not None
                        else LookupStatus.NOT_FOUND
                        if supported
                        else LookupStatus.UNSUPPORTED_IDENTIFIER
                    )

        # 真正部分成功包括确认未找到、歧义等结果；基础 metadata 不能掩盖 enrich 全部失败。
        usable = any(
            item.status
            in (
                LookupStatus.FOUND,
                LookupStatus.NOT_FOUND,
                LookupStatus.AMBIGUOUS,
                LookupStatus.MISSING_API_KEY,
            )
            for item in items
        )
        if not usable and first_error is not None:
            raise first_error
        return LookupResult(items, execution)

    async def _details(self, items, positions, api_key):
        """沿用精确详情的 OpenAlex 回填；此路径不会调用任何在线 enricher。"""
        attempts = failures = 0
        first_error = None
        for identity, indexes in positions.items():
            if identity.kind not in (
                IdentifierKind.ARXIV,
                IdentifierKind.DOI,
                IdentifierKind.OPENALEX,
            ):
                continue
            if items[indexes[0]].status != LookupStatus.NOT_FOUND:
                continue
            if api_key is None:
                if identity.kind == IdentifierKind.OPENALEX:
                    for index in indexes:
                        items[index].status = LookupStatus.MISSING_API_KEY
                    attempts += 1
                    failures += 1
                continue
            if self.openalex_lookup is None:
                raise ServiceException(ScholarErrorCode.CAPABILITY_UNAVAILABLE)
            attempts += 1
            try:
                paper = await self.openalex_lookup(identity, api_key=api_key)
            except ProviderError as error:
                failures += 1
                first_error = first_error or error
                for index in indexes:
                    items[index].status, items[index].error = (
                        LookupStatus.FAILED,
                        str(error),
                    )
            else:
                for index in indexes:
                    items[index].paper = deepcopy(paper)
                    items[index].status = (
                        LookupStatus.FOUND
                        if paper is not None
                        else LookupStatus.NOT_FOUND
                    )
        if not attempts:
            return [], first_error
        return [
            SourceExecution(
                PaperSource.OPENALEX,
                False,
                SourceExecutionStatus.FAILED
                if failures == attempts
                else SourceExecutionStatus.PARTIAL
                if failures
                else SourceExecutionStatus.COMPLETED,
                str(first_error) if first_error is not None else None,
            )
        ], first_error
