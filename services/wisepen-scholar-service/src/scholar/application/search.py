"""执行领域声明的搜索与富化计划，不按领域或供应商分支编排。"""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass, field, replace
from datetime import date
from enum import StrEnum

import orjson

from common.utils.retrieval import Candidate, RrfFusion
from common.core.exceptions import ServiceException
from scholar.domain.interfaces.field_policies import ScholarPolicyProvider
from scholar.application.lookup_runner import LookupRunner
from scholar.application.paper_projection import project_paper
from scholar.application.pdf_recover import PaperPdfRecoverService
from scholar.domain.errors import ProviderError, ScholarErrorCode, provider_operation
from scholar.domain.interfaces.paper_searcher import PaperSearcher
from scholar.domain.models.fields import AcademicField
from scholar.domain.models.identifiers import normalize_arxiv, normalize_doi
from scholar.domain.models.paper import Paper
from scholar.domain.models.source_execution import (
    SourceExecution,
    SourceExecutionStatus,
)
from scholar.domain.repositories.query_cache import QueryCacheRepository


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
    requires_byok: bool = False


class PaperSearchService:
    def __init__(
        self,
        searchers: dict[str, SearchBinding],
        policies: ScholarPolicyProvider,
        query_cache: QueryCacheRepository,
        lookup: LookupRunner,
        pdf: PaperPdfRecoverService,
        platform_credentials: dict[str, str] | None = None,
    ):
        self.platform_credentials = platform_credentials or {}
        self.searchers = searchers
        self.policies = policies
        self.query_cache = query_cache
        self.lookup_runner = lookup
        self.pdf = pdf

    async def search(
        self,
        query: PaperSearch,
        *,
        credentials: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> SearchResult:
        # 检索和富化共用截止时间；富化耗尽预算不能撤销已检索到的论文。
        deadline = asyncio.get_running_loop().time() + timeout if timeout is not None else None
        credentials = credentials or {}
        # 在首次 I/O 前固定快照，后续富化也使用同一版通用来源与领域策略。
        policies = self.policies.current
        policy = policies.fields[query.field]
        # 搜索的可用性先于缓存判断；平台凭证只在后续富化阶段使用。
        skipped = {
            source: SourceExecution(source, False, SourceExecutionStatus.NOT_EXECUTED, f"{source} search requires user BYOK")
            for source in policy.searchers
            if self.searchers[source].requires_byok and not credentials.get(source)
        }
        plan = [source for source in policy.searchers if source not in skipped]
        if not plan:
            raise ServiceException(ScholarErrorCode.CAPABILITY_UNAVAILABLE, "; ".join(item.error for item in skipped.values()))

        # 多源：并发请求；单源：按优先级回退，空结果也视为成功。
        if query.source_scope == SourceScope.MULTI_SOURCE:
            results = await asyncio.gather(
                *(
                    self._search_source(source, query, credentials.get(source), deadline)
                    for source in plan
                ),
                return_exceptions=True,
            )
        else:
            results = []
            for source in plan:
                try:
                    result = await self._search_source(
                        source, query, credentials.get(source), deadline
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

        # 全部来源失败时抛出首个错误。
        if len(failures) == len(results):
            raise failures[0]

        if query.source_scope == SourceScope.DEFAULT:
            # 单源只取最后一个被尝试来源的结果（即首个成功来源）。
            papers, conflicts = ranked[plan[len(results) - 1]][: query.limit], []
        else:
            papers, conflicts = self._fuse_sources(ranked, limit=query.limit)

        # 通用来源取得论文即完成获取；领域来源仍按详情完整性决定是否补查。
        succeeded = {
            source
            for source, result in zip(plan, results)
            if not isinstance(result, BaseException)
        }
        targets = [self.lookup_runner.target(None, paper) for paper in papers]
        enrichment_execution = await self.lookup_runner.run(
            targets,
            # 通用来源中已有成功者则跳过通用富化链，否则补跑尚未执行的通用来源。
            common_sources=(
                []
                if succeeded.intersection(policies.common_enrichers)
                else [
                    source
                    for source in policies.common_enrichers
                    if source in self.lookup_runner.bindings
                ]
            ),
            domain_sources=policy.field_enrichers,
            credentials=self.platform_credentials | credentials,
            deadline=deadline,
        )
        pdf_budget = asyncio.timeout_at(deadline)
        try:
            async with pdf_budget:
                papers, pdf_execution = await self.pdf.recover([target.paper for target in targets])
        except TimeoutError:
            if not pdf_budget.expired():
                raise
            papers = [target.paper for target in targets]
            pdf_execution = SourceExecution(
                "unpaywall", False, SourceExecutionStatus.FAILED,
                "PDF recovery exceeded the search request deadline",
            )
        if pdf_execution is not None:
            enrichment_execution.append(pdf_execution)
        enriched_papers = [project_paper(paper) for paper in papers]
        executions = skipped | {item.source: item for item in execution}
        execution = [executions[source] for source in policy.searchers if source in executions]
        return SearchResult(enriched_papers, execution, conflicts, enrichment_execution)

    async def _search_source(
        self,
        source: str,
        query: PaperSearch,
        api_key: str | None,
        deadline: float | None,
    ) -> tuple[list[Paper], SourceExecution]:
        binding = self.searchers[source]
        cache_key = self._cache_key(source, binding, query)

        budget = asyncio.timeout_at(deadline)
        stage = "query_cache.get"
        operation_token = provider_operation.set("search")
        try:
            async with budget:
                cached = await self.query_cache.get(cache_key)
                if cached is not None:
                    return cached, SourceExecution(source, True)

                # 缓存来源的一批候选；用户 limit 只在融合/返回时裁剪，避免改数量重新请求上游。
                stage = "search"
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
                    stage = "query_cache.put"
                    await self.query_cache.put(cache_key, batch.papers)
        except TimeoutError as error:
            if not budget.expired():
                raise
            operation = provider_operation.get() if stage == "search" else stage
            raise ProviderError(f"{source} {operation} exceeded the search request deadline") from error
        finally:
            provider_operation.reset(operation_token)
        return batch.papers, SourceExecution(
            source,
            False,
            (
                SourceExecutionStatus.PARTIAL
                if batch.partial_error
                else SourceExecutionStatus.COMPLETED
            ),
            batch.partial_error,
        )

    @staticmethod
    def _cache_key(
        source: str,
        binding: SearchBinding,
        query: PaperSearch,
    ) -> str:
        # 来源候选按查询条件共享；凭证只用于缓存未命中时的上游请求。
        identity = {
            "source": source,
            "revision": binding.cache_revision,
            "query": query.query,
            "field": query.field.value,
            "published_after": (
                None
                if query.published_after is None
                else query.published_after.isoformat()
            ),
            "published_before": (
                None
                if query.published_before is None
                else query.published_before.isoformat()
            ),
            "candidates": binding.candidate_limit,
        }
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
        cls,
        ranked: dict[str, list[Paper]],
        *,
        limit: int,
    ) -> tuple[list[Paper], list[str]]:
        """按精确身份关联任意来源数；歧义记录不通过 RRF 再次误合并。"""
        rows = [
            (source, rank, paper)
            for source, papers in ranked.items()
            for rank, paper in enumerate(papers)
        ]
        identities = [cls._paper_identity(paper) for _, _, paper in rows]

        # 1) 构建跨来源的精确身份关联图。
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
                            f"arXiv {left_arxiv} has conflicting DOIs; "
                            "merged by arXiv identity."
                        )
                elif same_doi:
                    if left_arxiv and right_arxiv and left_arxiv != right_arxiv:
                        conflicts.append(
                            f"DOI {left_doi} has conflicting arXiv identities; "
                            "records kept separate."
                        )
                        continue
                else:
                    continue
                neighbors[left].add(right)
                neighbors[right].add(left)

        # 2) 按连通分量合并；同一来源重复出现或 arXiv 身份不唯一时记录冲突。
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
                identities[member][0]
                for member in members
                if identities[member][0]
            }
            if len(set(sources)) != len(sources) or len(arxiv_ids) > 1:
                conflicts.append(
                    "An exact identifier is ambiguous across sources; "
                    "records kept separate."
                )
                for member in members:
                    candidates[member] = (f"record:{member}", rows[member][2])
                continue

            # 计划中先登记的来源提供基础事实，融合器不识别供应商。
            first, metadata = rows[members[0]][2], {}
            for member in members:
                for key, value in rows[member][2].metadata.items():
                    metadata.setdefault(key, value)

            arxiv_id = next(
                (identities[m][0] for m in members if identities[m][0]),
                None,
            )
            # 生成值只标识 arXiv 预印本；其他来源给出的正式出版 DOI 优先。
            dois = [identities[m][1] for m in members if identities[m][1]]
            doi = next(
                (
                    value
                    for value in dois
                    if value != f"10.48550/arxiv.{arxiv_id}"
                ),
                next(iter(dois), None),
            )
            merged = replace(
                first,
                arxiv_id=arxiv_id,
                doi=doi,
                metadata=metadata,
            )
            for member in members:
                candidates[member] = (f"record:{members[0]}", merged)

        # 3) 按融合器要求组织为来源列表，再交给 RRF。
        lists = {source: [] for source in ranked}
        for index, (source, rank, _) in enumerate(rows):
            identity, paper = candidates[index]
            lists[source].append(
                Candidate(candidate_id=identity, payload=paper, rank=rank + 1)
            )
        fused = RrfFusion[Paper]().fuse(list(lists.values()))
        return (
            [candidate.payload for candidate in fused[:limit]],
            list(dict.fromkeys(conflicts)),
        )
