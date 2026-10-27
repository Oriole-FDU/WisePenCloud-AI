"""执行领域声明的搜索与富化计划，不按领域或供应商分支编排。"""

from __future__ import annotations

import asyncio
import hashlib
from copy import deepcopy
from dataclasses import dataclass, field, replace
from datetime import date
from enum import StrEnum
from typing import Any

import orjson
from common.core.exceptions import ServiceException
from common.utils.retrieval import Candidate, RrfFusion
from redis.exceptions import RedisError

from scholar.application.field_policies import FieldPolicy
from scholar.application.source_execution import SourceExecution, SourceExecutionStatus
from scholar.domain.error_codes import ScholarErrorCode
from scholar.domain.interfaces.paper_enricher import PaperEnricher, PaperEnrichment
from scholar.domain.interfaces.paper_searcher import PaperSearcher
from scholar.domain.interfaces.provider_error import ProviderError
from scholar.domain.models.fields import AcademicField
from scholar.domain.models.identifiers import (
    IdentifierKind,
    PaperIdentifier,
    normalize_arxiv,
    normalize_doi,
    parse_identifier,
)
from scholar.domain.models.paper import Paper
from scholar.domain.repositories.paper_enrichment_cache import (
    CachedPaperEnrichment,
    PaperEnrichmentCache,
)
from scholar.domain.repositories.query_cache_repository import QueryCacheRepository


class SourceScope(StrEnum):
    DEFAULT = "default"
    MULTI_SOURCE = "multi_source"


@dataclass
class PaperSearch:
    query: str
    field: AcademicField = AcademicField.GENERAL
    published_after: date | None = None
    published_before: date | None = None
    limit: int = 10
    source_scope: SourceScope = SourceScope.DEFAULT


@dataclass
class SearchResult:
    papers: list[Paper]
    execution: list[SourceExecution]
    identity_conflicts: list[str]
    enrichment_execution: list[SourceExecution] = field(default_factory=list)


@dataclass
class SearchBinding:
    """能力注册同时声明用户凭证前提与来源覆盖说明。"""

    searcher: PaperSearcher
    requires_api_key: bool = False
    coverage: str = ""


@dataclass
class EnrichmentBinding:
    """一个配置来源的身份覆盖、缓存读取与可选富化能力。"""

    enricher: PaperEnricher | None
    cache: PaperEnrichmentCache | None
    identifier_kinds: list[IdentifierKind]
    coverage: str = ""
    fields: list[str] = field(default_factory=list)


class PaperSearchService:
    _IDENTIFIER_PRIORITY = (
        IdentifierKind.ARXIV,
        IdentifierKind.DOI,
        IdentifierKind.CORPUS_ID,
        IdentifierKind.S2,
    )

    def __init__(
        self,
        searchers: dict[str, SearchBinding],
        policies: dict[AcademicField, FieldPolicy],
        query_cache: QueryCacheRepository,
        enrichment_bindings: dict[str, EnrichmentBinding],
        *,
        positive_ttl_seconds: int = 86400,
        negative_ttl_seconds: int = 3600,
        concurrency: int = 4,
    ):
        self.searchers = searchers
        self.policies = policies
        self.query_cache = query_cache
        self.enrichment_bindings = enrichment_bindings
        self.positive_ttl_seconds = positive_ttl_seconds
        self.negative_ttl_seconds = negative_ttl_seconds
        # 共享实例限制并发请求用例同时进入来源富化端口的数量。
        self._provider_slots = asyncio.Semaphore(concurrency)
        self._cache_slots = asyncio.Semaphore(concurrency)

    async def search(
        self,
        query: PaperSearch,
        *,
        credentials: dict[str, str] | None = None,
        user_id: str | None = None,
    ) -> SearchResult:
        credentials = credentials or {}
        policy = self.policies[query.field]
        plan = policy.searchers
        if query.source_scope == SourceScope.MULTI_SOURCE:
            results = await asyncio.gather(
                *(self._search_source(source, query, credentials.get(source), user_id)
                  for source in plan),
                return_exceptions=True,
            )
        else:
            # 正常空列表也代表请求成功；仅外部来源失败触发下一个优先级。
            results = []
            for source in plan:
                try:
                    result = await self._search_source(
                        source, query, credentials.get(source), user_id
                    )
                except ProviderError as error:
                    results.append(error)
                else:
                    results.append(result)
                    break
        ranked, execution, failures = {}, [], []
        for source, result in zip(plan[:len(results)], results, strict=True):
            if isinstance(result, BaseException):
                # 只隔离已知外部失败；程序错误和取消直接传播。
                if not isinstance(result, ProviderError):
                    raise result
                failures.append(result)
                ranked[source] = []
                execution.append(
                    SourceExecution(
                        source,
                        False,
                        self.searchers[source].coverage,
                        SourceExecutionStatus.FAILED,
                        str(result),
                    )
                )
            else:
                ranked[source] = result[0]
                execution.append(result[1])
        if len(failures) == len(results):
            raise failures[0]
        if query.source_scope == SourceScope.DEFAULT:
            papers, conflicts = ranked[plan[len(results) - 1]][: query.limit], []
        else:
            papers, conflicts = self._fuse_sources(ranked, limit=query.limit)
        # 只处理最终候选；召回缓存不存领域专有富化或上次执行状态。
        enriched_papers, enrichment_execution = await self._enrich_candidates(
            papers, policy.enrichers
        )
        return SearchResult(
            enriched_papers, execution, conflicts, enrichment_execution
        )

    async def _search_source(
        self, source: str, query: PaperSearch, api_key: str | None, user_id: str | None
    ) -> tuple[list[Paper], SourceExecution]:
        binding = self.searchers[source]
        cache_key = self._cache_key(source, binding, query, api_key, user_id)
        if cache_key is not None:
            cached = await self.query_cache.get(cache_key)
            if cached is not None:
                return cached, SourceExecution(source, True, binding.coverage)
        papers = await binding.searcher.search(
            query=query.query,
            field=query.field,
            published_after=query.published_after,
            published_before=query.published_before,
            limit=query.limit,
            api_key=api_key,
        )
        if cache_key is not None:
            await self.query_cache.put(cache_key, papers)
        return papers, SourceExecution(source, False, binding.coverage)

    @staticmethod
    def _cache_key(
        source: str,
        binding: SearchBinding,
        query: PaperSearch,
        api_key: str | None,
        user_id: str | None,
    ) -> str | None:
        if binding.requires_api_key and (user_id is None or api_key is None):
            return None
        identity = {
            "source": source,
            "query": query.query,
            "field": query.field.value,
            "published_after": None
            if query.published_after is None
            else query.published_after.isoformat(),
            "published_before": None
            if query.published_before is None
            else query.published_before.isoformat(),
            "limit": query.limit,
        }
        if binding.requires_api_key:
            identity["user_id"] = user_id
            identity["credential_fingerprint"] = hashlib.sha256(
                api_key.encode()
            ).hexdigest()
        digest = hashlib.sha256(
            orjson.dumps(identity, option=orjson.OPT_SORT_KEYS)
        ).hexdigest()
        return f"{source}:{digest}"

    @staticmethod
    def _paper_identity(paper: Paper) -> tuple[str | None, str | None]:
        return (
            normalize_arxiv(paper.arxiv_id) if paper.arxiv_id else None,
            normalize_doi(paper.doi) if paper.doi else None,
        )

    @classmethod
    def _fuse_sources(
        cls, ranked: dict[str, list[Paper]], *, limit: int
    ) -> tuple[list[Paper], list[str]]:
        """按精确身份关联任意来源数；歧义记录不通过 RRF 再次误合并。"""
        rows = [
            (source, rank, paper)
            for source, papers in ranked.items()
            for rank, paper in enumerate(papers)
        ]
        identities = [cls._paper_identity(paper) for _, _, paper in rows]
        neighbors = [set() for _ in rows]
        conflicts = []
        for left, (left_source, _, _) in enumerate(rows):
            left_arxiv, left_doi = identities[left]
            for right in range(left + 1, len(rows)):
                if left_source == rows[right][0]:
                    continue
                right_arxiv, right_doi = identities[right]
                same_arxiv = left_arxiv is not None and left_arxiv == right_arxiv
                same_doi = left_doi is not None and left_doi == right_doi
                if same_arxiv:
                    if left_doi and right_doi and left_doi != right_doi:
                        conflicts.append(
                            f"arXiv {left_arxiv} has conflicting DOIs; merged by arXiv identity."
                        )
                elif same_doi:
                    if left_arxiv and right_arxiv and left_arxiv != right_arxiv:
                        conflicts.append(
                            f"DOI {left_doi} has conflicting arXiv identities; records kept separate."
                        )
                        continue
                else:
                    continue
                neighbors[left].add(right)
                neighbors[right].add(left)

        candidates, seen = {}, set()
        for index in range(len(rows)):
            if index in seen:
                continue
            component, todo = set(), [index]
            while todo:
                member = todo.pop()
                if member in component:
                    continue
                component.add(member)
                todo.extend(neighbors[member] - component)
            seen.update(component)
            members = sorted(component)
            sources = [rows[member][0] for member in members]
            arxiv_ids = {
                identities[member][0] for member in members if identities[member][0]
            }
            if len(set(sources)) != len(sources) or len(arxiv_ids) > 1:
                conflicts.append(
                    "An exact identifier is ambiguous across sources; records kept separate."
                )
                for member in members:
                    candidates[member] = (f"record:{member}", rows[member][2])
                continue
            # 计划中先登记的来源提供基础事实，融合器不识别供应商。
            first, metadata = rows[members[0]][2], {}
            for member in members:
                for key, value in rows[member][2].metadata.items():
                    metadata.setdefault(key, value)
            merged = replace(
                first,
                arxiv_id=next(
                    (identities[m][0] for m in members if identities[m][0]), None
                ),
                doi=next((identities[m][1] for m in members if identities[m][1]), None),
                metadata=metadata,
            )
            for member in members:
                candidates[member] = (f"record:{members[0]}", merged)
        lists = {source: [] for source in ranked}
        for index, (source, rank, _) in enumerate(rows):
            identity, paper = candidates[index]
            lists[source].append(
                Candidate(candidate_id=identity, payload=paper, rank=rank + 1)
            )
        fused = RrfFusion[Paper]().fuse(list(lists.values()))
        return [candidate.payload for candidate in fused[:limit]], list(
            dict.fromkeys(conflicts)
        )

    async def _enrich_candidates(
        self, papers: list[Paper], sources: list[str]
    ) -> tuple[list[Paper], list[SourceExecution]]:
        """按来源配置顺序合并信号；输入候选不会被原地修改或重排。"""
        enriched_papers = deepcopy(papers)
        executions = []
        for source in sources:
            binding = self.enrichment_bindings[source]
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
        return enriched_papers, executions

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


__all__ = [
    "EnrichmentBinding",
    "PaperSearch",
    "PaperSearchService",
    "SearchBinding",
    "SearchResult",
    "SourceScope",
]
