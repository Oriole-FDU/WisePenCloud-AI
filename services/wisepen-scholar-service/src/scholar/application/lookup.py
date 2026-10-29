"""精确批量 lookup 用例；统一获取完整事实，返回基础信息及调研 metadata。"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from enum import StrEnum

from scholar.application.field_policies import (
    COMMON_ENRICHERS,
    FIELD_POLICIES,
    FieldPolicy,
)
from scholar.application.lookup_runner import LookupRunner
from scholar.application.paper_projection import project_paper
from scholar.application.pdf_recover import PaperPdfRecoverService
from scholar.domain.models.fields import AcademicField
from scholar.domain.models.identifiers import PaperIdentifier, parse_identifier
from scholar.domain.models.paper import Paper
from scholar.domain.models.source_execution import SourceExecution


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
    field: AcademicField = AcademicField.GENERAL


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
    """保留输入位置、身份歧义和部分成功；不依赖仓储或单独的来源后门。"""

    def __init__(
        self,
        lookup: LookupRunner,
        pdf: PaperPdfRecoverService,
        *,
        policies: dict[AcademicField, FieldPolicy] | None = None,
        platform_credentials: dict[str, str] | None = None,
    ):
        self.lookup_runner = lookup
        self.pdf = pdf
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
        confirmed = [target for target in targets if target.paper is not None and not target.ambiguous]
        papers, pdf_execution = await self.pdf.recover([target.paper for target in confirmed])
        for target, paper in zip(confirmed, papers, strict=True):
            target.paper = paper
        if pdf_execution is not None:
            execution.append(pdf_execution)
        first_error = None
        for identity, target in zip(positions, targets, strict=True):
            if target.errors:
                first_error = first_error or target.errors[0]
            supported = any(
                alias.kind in self.lookup_runner.bindings[source].rules.identifier_kinds
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

            # 已成功回退的通用错误保留在 execution；仍影响增量的错误也放在论文项上。
            error = (
                target.domain_errors[0]
                if target.domain_errors
                else target.errors[0]
                if target.errors and not target.general_found
                else None
            )
            # 歧义属于整个身份查询的结果，不把此前的基础论文作为已确认匹配返回。
            paper = project_paper(target.paper) if target.paper is not None and not target.ambiguous else None
            for index in positions[identity]:
                item = items[index]
                item.status = status
                item.error = str(error) if error is not None else None
                item.paper = deepcopy(paper)

        # 可用性基于查询结果；保留找到的论文和身份歧义，只有整批失败才传播错误。
        if first_error is not None and not any(
            item.status
            in (LookupStatus.FOUND, LookupStatus.NOT_FOUND, LookupStatus.AMBIGUOUS)
            for item in items
        ):
            raise first_error
        return LookupResult(items, execution)
