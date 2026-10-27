"""检索用例内部的候选后处理：按配置补充来源事实。"""

import asyncio
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any

from redis.exceptions import RedisError

from scholar.application.source_execution import (
    SourceExecution,
    SourceExecutionStatus,
)
from scholar.domain.interfaces.paper_enricher import (
    PaperEnricher,
    PaperEnrichment,
)
from scholar.domain.interfaces.provider_error import ProviderError
from scholar.domain.models.identifiers import (
    IdentifierKind,
    PaperIdentifier,
    parse_identifier,
)
from scholar.domain.models.paper import Paper
from scholar.domain.repositories.paper_enrichment_cache import (
    CachedPaperEnrichment,
    PaperEnrichmentCache,
)


@dataclass
class EnrichmentBinding:
    """一个配置来源的身份覆盖、缓存读取与可选富化能力。"""

    enricher: PaperEnricher | None
    cache: PaperEnrichmentCache | None
    identifier_kinds: list[IdentifierKind]
    coverage: str = ""
    fields: list[str] = field(default_factory=list)


@dataclass
class CandidateEnrichmentResult:
    """保留候选顺序和数量，并逐来源报告富化执行情况。"""

    papers: list[Paper]
    execution: list[SourceExecution]


class _CandidateEnrichment:
    """顺序执行配置来源；缓存和外部失败不会丢弃基础候选。"""

    _IDENTIFIER_PRIORITY = (
        IdentifierKind.ARXIV,
        IdentifierKind.DOI,
        IdentifierKind.CORPUS_ID,
        IdentifierKind.S2,
    )

    def __init__(
        self,
        bindings: dict[str, EnrichmentBinding],
        *,
        positive_ttl_seconds: int = 24 * 60 * 60,
        negative_ttl_seconds: int = 60 * 60,
        concurrency: int = 4,
    ):
        self.bindings = bindings
        self.positive_ttl_seconds = positive_ttl_seconds
        self.negative_ttl_seconds = negative_ttl_seconds
        # 共享实例限制并发请求用例同时进入来源富化端口的数量。
        self._provider_slots = asyncio.Semaphore(concurrency)
        self._cache_slots = asyncio.Semaphore(concurrency)

    async def enrich(
        self, papers: list[Paper], sources: list[str]
    ) -> CandidateEnrichmentResult:
        """按来源配置顺序合并信号；输入候选不会被原地修改或重排。"""
        enriched_papers = deepcopy(papers)
        executions = []
        for source in sources:
            binding = self.bindings[source]
            positions: dict[PaperIdentifier, list[int]] = {}
            for index, paper in enumerate(enriched_papers):
                known_fields = paper.metadata.get(source, {})
                if binding.fields and all(
                    name in known_fields for name in binding.fields
                ):
                    continue
                identifier = self._select_identifier(paper, binding.identifier_kinds)
                if identifier is not None:
                    positions.setdefault(identifier, []).append(index)
            executions.append(
                await self._enrich_source(source, binding, positions, enriched_papers)
            )
        return CandidateEnrichmentResult(enriched_papers, executions)

    async def _enrich_source(
        self,
        source: str,
        binding: EnrichmentBinding,
        positions: dict[PaperIdentifier, list[int]],
        papers: list[Paper],
    ) -> SourceExecution:
        successes: set[PaperIdentifier] = set()
        failures: set[PaperIdentifier] = set()
        cache_hits: set[PaperIdentifier] = set()
        pending = []
        error = None

        if not positions:
            return SourceExecution(
                source=source,
                cache_hit=False,
                coverage=binding.coverage,
                status=SourceExecutionStatus.NOT_EXECUTED,
            )

        for identifier, indexes in positions.items():
            cached = None
            if binding.cache is not None:
                try:
                    async with self._cache_slots:
                        cached = await binding.cache.get([identifier])
                except RedisError as exc:
                    failures.add(identifier)
                    error = error or str(exc)
                    continue

            if cached is None:
                if binding.enricher is not None:
                    pending.append(identifier)
                continue

            cache_hits.add(identifier)
            if not cached.record_found:
                successes.add(identifier)
                continue
            enrichment = cached.enrichment
            if self._conflicts(identifier, indexes, papers, enrichment):
                failures.add(identifier)
                error = error or "Cached enrichment has conflicting paper identifiers"
                continue
            self._merge_into_candidates(indexes, papers, enrichment)
            successes.add(identifier)

        if pending:
            try:
                async with self._provider_slots:
                    responses = await binding.enricher.enrich(
                        pending, include_details=False
                    )
            except ProviderError as exc:
                # 可选后处理失败仍保留已召回论文；程序错误和取消直接传播。
                failures.update(pending)
                error = error or str(exc)
            else:
                for identifier, enrichment in zip(pending, responses, strict=True):
                    if enrichment is not None and self._conflicts(
                        identifier, positions[identifier], papers, enrichment
                    ):
                        failures.add(identifier)
                        error = error or "Enrichment has conflicting paper identifiers"
                        continue

                    successes.add(identifier)
                    aliases = (identifier,)
                    if enrichment is not None:
                        aliases = tuple(
                            dict.fromkeys((identifier, *enrichment.identifiers))
                        )
                        self._merge_into_candidates(
                            positions[identifier], papers, enrichment
                        )

                    if binding.cache is not None:
                        record = CachedPaperEnrichment(
                            enrichment=enrichment,
                            cache_aliases=aliases,
                            record_found=enrichment is not None,
                            details_confirmed=(
                                enrichment is not None and enrichment.paper is not None
                            ),
                        )
                        ttl = (
                            self.positive_ttl_seconds
                            if enrichment is not None
                            else self.negative_ttl_seconds
                        )
                        try:
                            await binding.cache.put(
                                list(aliases), record, ttl_seconds=ttl
                            )
                        except RedisError as exc:
                            failures.add(identifier)
                            error = error or str(exc)

        unexecuted = len(positions) - len(successes | failures)
        if failures:
            status = (
                SourceExecutionStatus.PARTIAL
                if successes
                else SourceExecutionStatus.FAILED
            )
        elif unexecuted:
            status = (
                SourceExecutionStatus.PARTIAL
                if successes
                else SourceExecutionStatus.NOT_EXECUTED
            )
        else:
            status = SourceExecutionStatus.COMPLETED
        return SourceExecution(
            source=source,
            cache_hit=bool(cache_hits),
            coverage=binding.coverage,
            status=status,
            error=error,
        )

    @classmethod
    def _select_identifier(
        cls, paper: Paper, supported: list[IdentifierKind]
    ) -> PaperIdentifier | None:
        available = {
            identifier.kind: identifier for identifier in cls._identifiers(paper)
        }
        supported_kinds = set(supported)
        return next(
            (
                available[kind]
                for kind in cls._IDENTIFIER_PRIORITY
                if kind in supported_kinds and kind in available
            ),
            None,
        )

    @staticmethod
    def _identifiers(paper: Paper) -> list[PaperIdentifier]:
        identifiers = []
        if paper.arxiv_id is not None:
            identifiers.append(parse_identifier(paper.arxiv_id))
        if paper.doi is not None:
            identifiers.append(parse_identifier(paper.doi))
        return identifiers

    @classmethod
    def _conflicts(
        cls,
        requested: PaperIdentifier,
        indexes: list[int],
        papers: list[Paper],
        enrichment: PaperEnrichment,
    ) -> bool:
        response_ids = enrichment.identifiers
        observed: dict[IdentifierKind, str] = {}
        for identifier in response_ids:
            previous = observed.setdefault(identifier.kind, identifier.value)
            if previous != identifier.value:
                return True
            if (
                identifier.kind == requested.kind
                and identifier.value != requested.value
            ):
                return True
        return any(
            known.kind in observed and observed[known.kind] != known.value
            for index in indexes
            for known in cls._identifiers(papers[index])
        )

    @classmethod
    def _merge_into_candidates(
        cls,
        indexes: list[int],
        papers: list[Paper],
        enrichment: PaperEnrichment,
    ) -> None:
        for index in indexes:
            paper = papers[index]
            for namespace, values in enrichment.metadata.items():
                paper.metadata[namespace] = cls._merge_value(
                    paper.metadata.get(namespace), values
                )

    @classmethod
    def _merge_value(cls, existing: Any, incoming: Any) -> Any:
        if isinstance(existing, dict) and isinstance(incoming, dict):
            merged = deepcopy(existing)
            for key, value in incoming.items():
                if key not in merged or merged[key] is None:
                    merged[key] = deepcopy(value)
                elif isinstance(merged[key], dict) and isinstance(value, dict):
                    merged[key] = cls._merge_value(merged[key], value)
            return merged
        return deepcopy(incoming if existing is None else existing)
