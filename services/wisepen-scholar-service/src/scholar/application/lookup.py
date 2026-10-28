"""精确批量 lookup 用例；统一获取完整事实，purpose 仅控制返回投影。"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from scholar.application.field_policies import (
    COMMON_ENRICHERS,
    FIELD_POLICIES,
    FieldPolicy,
)
from scholar.application.lookup_runner import LookupRunner
from scholar.application.source_execution import SourceExecution
from scholar.domain.models.fields import AcademicField
from scholar.domain.models.identifiers import PaperIdentifier, parse_identifier
from scholar.domain.models.paper import Paper
from scholar.providers.projection import project_paper


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
    """保留输入位置、身份歧义和部分成功；不依赖仓储或单独的来源后门。"""

    def __init__(
        self,
        lookup: LookupRunner,
        *,
        policies: dict[AcademicField, FieldPolicy] | None = None,
        platform_credentials: dict[str, str] | None = None,
    ):
        self.lookup_runner = lookup
        self.policies = FIELD_POLICIES if policies is None else policies
        self.platform_credentials = platform_credentials or {}

    async def lookup(
        self,
        query: PaperLookup,
        *,
        credentials: dict[str, str] | None = None,
    ) -> LookupResult:
        credentials = self.platform_credentials | (credentials or {})
        positions: dict[PaperIdentifier, list[int]] = {}
        items = []
        for value in query.identifiers:
            try:
                identity = parse_identifier(value)
            except ValueError:
                items.append(LookupItem(value, LookupStatus.INVALID_IDENTIFIER))
                continue
            positions.setdefault(identity, []).append(len(items))
            items.append(LookupItem(value, LookupStatus.NOT_FOUND))

        targets = [self.lookup_runner.target(identity) for identity in positions]
        policy = self.policies[query.field]
        sources = [
            source
            for source in COMMON_ENRICHERS
            if source in self.lookup_runner.bindings
        ]
        execution = await self.lookup_runner.run(
            targets,
            common_sources=sources,
            domain_sources=policy.field_enrichers,
            credentials=credentials,
        )
        first_error = None
        for identity, target in zip(positions, targets, strict=True):
            if target.errors:
                first_error = first_error or target.errors[0]
            supported = any(
                alias.kind in self.lookup_runner.bindings[source].identifier_kinds
                for source in [*sources, *policy.field_enrichers]
                for alias in target.identifiers
            )
            if target.ambiguous:
                status = LookupStatus.AMBIGUOUS
            elif target.paper is not None:
                status = LookupStatus.FOUND
            elif target.errors:
                status = LookupStatus.FAILED
            else:
                status = (
                    LookupStatus.NOT_FOUND
                    if supported
                    else LookupStatus.UNSUPPORTED_IDENTIFIER
                )

            if target.has_signals:
                enrichment_status = (
                    EnrichmentStatus.COMPLETE
                    if target.general_found and not target.domain_errors
                    else EnrichmentStatus.PARTIAL
                )
            elif target.paper is not None:
                enrichment_status = EnrichmentStatus.PARTIAL
            elif target.errors:
                enrichment_status = EnrichmentStatus.FAILED
            else:
                enrichment_status = (
                    EnrichmentStatus.NOT_FOUND
                    if supported
                    else EnrichmentStatus.UNSUPPORTED
                )
            # 已成功回退的通用错误保留在 execution；仍影响增量的错误也放在论文项上。
            error = (
                target.domain_errors[0]
                if target.domain_errors
                else target.errors[0]
                if target.errors and not target.general_found
                else None
            )
            # 歧义属于整个身份查询的结果，不把此前的基础论文作为已确认匹配返回。
            paper = (
                project_paper(target.paper)
                if target.paper is not None and not target.ambiguous
                else None
            )
            for index in positions[identity]:
                item = items[index]
                item.status = status
                item.error = str(error) if error is not None else None
                item.metadata = (
                    deepcopy(paper.metadata) or None if paper is not None else None
                )
                item.paper = (
                    deepcopy(paper) if query.purpose != LookupPurpose.ENRICH else None
                )
                if query.purpose != LookupPurpose.DETAILS and not target.ambiguous:
                    item.enrichment_status = enrichment_status

        # 可用性基于裁剪前的查询状态；enrich 不再因隐藏 Paper 而把部分成功变成整批失败。
        if first_error is not None and not any(
            item.status
            in (LookupStatus.FOUND, LookupStatus.NOT_FOUND, LookupStatus.AMBIGUOUS)
            for item in items
        ):
            raise first_error
        return LookupResult(items, execution)
