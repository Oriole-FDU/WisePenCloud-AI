"""执行领域声明的搜索与富化计划，不按领域或供应商分支编排。"""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass, field, replace
from datetime import date
from enum import StrEnum

import orjson
from common.utils.retrieval import Candidate, RrfFusion

from scholar.application.field_policies import COMMON_ENRICHERS, FieldPolicy
from scholar.application.lookup_runner import LookupRunner
from scholar.application.source_execution import SourceExecution, SourceExecutionStatus
from scholar.domain.errors import ProviderError
from scholar.domain.interfaces.paper_searcher import PaperSearcher
from scholar.domain.models.fields import AcademicField
from scholar.domain.models.identifiers import (
    normalize_arxiv,
    normalize_doi,
)
from scholar.domain.models.paper import Paper
from scholar.domain.repositories.query_cache import QueryCacheRepository
from scholar.providers.projection import project_paper


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
    """注册来源能力及结果缓存版本。"""

    searcher: PaperSearcher
    cache_revision: str = "2"
    candidate_limit: int = 100


class PaperSearchService:
    def __init__(
        self,
        searchers: dict[str, SearchBinding],
        policies: dict[AcademicField, FieldPolicy],
        query_cache: QueryCacheRepository,
        lookup: LookupRunner,
        platform_credentials: dict[str, str] | None = None,
    ):
        self.platform_credentials = platform_credentials or {}
        self.searchers = searchers
        self.policies = policies
        self.query_cache = query_cache
        self.lookup_runner = lookup

    async def search(
        self,
        query: PaperSearch,
        *,
        credentials: dict[str, str] | None = None,
        user_id: str | None = None,
    ) -> SearchResult:
        credentials = self.platform_credentials | (credentials or {})
        policy = self.policies[query.field]
        plan = policy.searchers
        if query.source_scope == SourceScope.MULTI_SOURCE:
            results = await asyncio.gather(
                *(
                    self._search_source(source, query, credentials.get(source), user_id)
                    for source in plan
                ),
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
        for source, result in zip(plan[: len(results)], results, strict=True):
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
        # 只排除本次成功来源；注册过但未执行或失败的来源仍可提供增量。
        succeeded = {
            source
            for source, result in zip(plan, results)
            if not isinstance(result, BaseException)
        }
        targets = [self.lookup_runner.target(None, paper) for paper in papers]
        enrichment_execution = await self.lookup_runner.run(
            targets,
            common_sources=[]
            if succeeded.intersection(COMMON_ENRICHERS)
            else [
                source
                for source in COMMON_ENRICHERS
                if source in self.lookup_runner.bindings
            ],
            domain_sources=[
                source for source in policy.field_enrichers if source not in succeeded
            ],
            credentials=credentials,
        )
        enriched_papers = [project_paper(target.paper) for target in targets]
        return SearchResult(enriched_papers, execution, conflicts, enrichment_execution)

    async def _search_source(
        self, source: str, query: PaperSearch, api_key: str | None, user_id: str | None
    ) -> tuple[list[Paper], SourceExecution]:
        binding = self.searchers[source]
        cache_key = self._cache_key(source, binding, query, api_key, user_id)
        cached = await self.query_cache.get(cache_key)
        if cached is not None:
            return cached, SourceExecution(source, True)
        # 缓存来源的一批候选；用户 limit 只在融合/返回时裁剪，避免改数量重新请求上游。
        batch = await binding.searcher.search(
            query=query.query,
            field=query.field,
            published_after=query.published_after,
            published_before=query.published_before,
            limit=binding.candidate_limit,
            api_key=api_key,
        )
        # 降级结果仍可返回，但不缓存为完整检索，避免编码恢复后继续命中旧降级结果。
        if batch.partial_error is None:
            await self.query_cache.put(cache_key, batch.papers)
        return batch.papers, SourceExecution(
            source,
            False,
            SourceExecutionStatus.PARTIAL
            if batch.partial_error
            else SourceExecutionStatus.COMPLETED,
            batch.partial_error,
        )

    @staticmethod
    def _cache_key(
        source: str,
        binding: SearchBinding,
        query: PaperSearch,
        api_key: str | None,
        user_id: str | None,
    ) -> str:
        identity = {
            "source": source,
            "revision": binding.cache_revision,
            "query": query.query,
            "field": query.field.value,
            "published_after": None
            if query.published_after is None
            else query.published_after.isoformat(),
            "published_before": None
            if query.published_before is None
            else query.published_before.isoformat(),
            "candidates": binding.candidate_limit,
        }
        if api_key:
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
