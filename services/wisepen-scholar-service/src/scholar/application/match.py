"""批量完整标题匹配：完整论文缓存 → 本地论文 → Crossref DOI → lookup。"""

from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import dataclass, field
from enum import StrEnum

from redis.exceptions import RedisError
from common.logger import warning

from scholar.application.lookup import LookupStatus, PaperLookup, PaperLookupService
from scholar.application.paper_projection import project_paper
from scholar.application.limitations import ResultLimitation
from scholar.core.providers.arxiv import with_arxiv_doi
from scholar.core.providers.policies import paper_identifiers
from scholar.domain.errors import ProviderError
from scholar.domain.interfaces.title_resolver import TitleMatch, TitleResolver
from scholar.domain.models.fields import AcademicField
from scholar.domain.models.identifiers import IdentifierKind, merge_identity_groups
from scholar.domain.models.paper import Paper
from scholar.domain.repositories.local_paper_repository import LocalPaperRepository
from scholar.domain.repositories.paper_cache import CachedPaperLookup, PaperCache
from scholar.utils import normalize_title


class MatchStatus(StrEnum):
    FOUND = "found"
    NOT_FOUND = "not_found"
    AMBIGUOUS = "ambiguous"
    FAILED = "failed"


@dataclass
class PaperMatch:
    """完整标题批量输入；field 只选择后续论文富化。"""

    titles: list[str]
    field: AcademicField = AcademicField.GENERAL


@dataclass
class MatchItem:
    """与一个原始标题位置对应的公共结果；歧义不任选一篇论文。"""

    title: str
    status: MatchStatus = MatchStatus.NOT_FOUND
    paper: Paper | None = None
    limitations: list[ResultLimitation] = field(default_factory=list)


@dataclass
class MatchResult:
    """保留标题顺序、重复项及逐项失败的公共响应。"""

    items: list[MatchItem]


class PaperMatchService:
    """逐项保留已找到的完整论文，只为未解决的标题访问下一层。"""

    def __init__(
        self,
        cache: PaperCache,
        local: LocalPaperRepository,
        crossref: TitleResolver,
        lookup: PaperLookupService,
        ttl: int = 86400,
    ):
        self.cache, self.local, self.crossref, self.lookup_service = (
            cache,
            local,
            crossref,
            lookup,
        )
        self.ttl = ttl

    async def match(
        self,
        query: PaperMatch,
        *,
        credentials: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> MatchResult:
        deadline = (
            asyncio.get_running_loop().time() + timeout
            if timeout is not None
            else None
        )

        # 归一化后去重；positions 保留每个唯一标题对应的原始位置。
        positions: dict[str, list[int]] = {}
        for index, title in enumerate(query.titles):
            positions.setdefault(normalize_title(title), []).append(index)
        titles = [query.titles[indexes[0]] for indexes in positions.values()]
        resolved = [MatchItem(title) for title in titles]
        writes, title_entries = [], []

        # 参与标题匹配的来源：通用富化来源 + 当前领域的专用来源。
        policies = self.lookup_service.policies.current
        sources = list(
            dict.fromkeys(
                [
                    *policies.common_enrichers,
                    *policies.fields[query.field].field_enrichers,
                ]
            )
        )

        # 完整论文缓存：命中即拿到完整论文，未命中留待后续层。
        try:
            async with asyncio.timeout_at(deadline):
                cached = await self.cache.match_titles(titles, sources)
            for item, match in zip(resolved, cached, strict=True):
                if match.error:
                    warning("Scholar title cache failed.", source="scholar", exc=match.error)
                    item.limitations.append(ResultLimitation.from_error("scholar", match.error))
                    item.status = MatchStatus.FAILED
                elif len(match.papers) > 1:
                    item.status = MatchStatus.AMBIGUOUS
                elif match.papers:
                    item.status = MatchStatus.FOUND
                    item.paper = project_paper(match.papers[0])
        except (RedisError, TimeoutError) as error:
            warning("Scholar title cache failed.", source="scholar", exc=error)

        # 本地论文索引：只处理仍未确定（非 FOUND / AMBIGUOUS）的标题。
        pending = [
            i
            for i, item in enumerate(resolved)
            if item.status not in (MatchStatus.FOUND, MatchStatus.AMBIGUOUS)
        ]
        if pending:
            try:
                async with asyncio.timeout_at(deadline):
                    local = await self.local.match_titles(
                        [titles[i] for i in pending]
                    )
                for index, match in zip(pending, local, strict=True):
                    # 本地可能有多篇论文共享同一身份组，合并后用于歧义判断。
                    candidates = merge_identity_groups(
                        [paper_identifiers(paper) for paper in match.papers]
                    )
                    if match.error:
                        warning("Scholar local title match failed.", source="arxiv", exc=match.error)
                        resolved[index].status = MatchStatus.FAILED
                        resolved[index].limitations.append(ResultLimitation.from_error("arxiv", match.error))
                    elif len(candidates) > 1:
                        resolved[index] = MatchItem(
                            titles[index], MatchStatus.AMBIGUOUS
                        )
                    elif match.papers:
                        # 仓储已给出正文，直接复用；保持 arXiv 既有 DOI 规范化合同。
                        paper = with_arxiv_doi(match.papers[0])
                        aliases = paper_identifiers(paper)
                        writes.append(
                            (CachedPaperLookup(paper, tuple(aliases)), self.ttl)
                        )
                        title_entries.append((titles[index], aliases))
                        resolved[index] = MatchItem(
                            titles[index],
                            MatchStatus.FOUND,
                            project_paper(paper),
                        )
            except (ProviderError, TimeoutError) as error:
                warning("Scholar local title match failed.", source="arxiv", exc=error)
                for index in pending:
                    resolved[index].status = MatchStatus.FAILED
                    resolved[index].limitations.append(ResultLimitation.from_error("arxiv", error))

        # Crossref DOI：把标题解析为稳定身份，只对仍未确定的标题请求。
        pending = [
            i
            for i, item in enumerate(resolved)
            if item.status not in (MatchStatus.FOUND, MatchStatus.AMBIGUOUS)
        ]
        if pending:
            try:
                matches = await self.crossref.match(
                    [titles[i] for i in pending], deadline=deadline
                )
            except (ProviderError, TimeoutError) as error:
                matches = [
                    TitleMatch(error=error) for _ in pending
                ]

            ready, identities = [], []
            for index, match in zip(pending, matches, strict=True):
                candidates = merge_identity_groups(match.candidates)
                if match.error:
                    warning("Scholar Crossref title match failed.", source="crossref", exc=match.error)
                    resolved[index].status = MatchStatus.FAILED
                    resolved[index].limitations.append(ResultLimitation.from_error("crossref", match.error))
                elif len(candidates) > 1:
                    resolved[index] = MatchItem(
                        titles[index], MatchStatus.AMBIGUOUS
                    )
                elif candidates:
                    # 同一论文优先 arXiv，其次 DOI，再其他稳定身份。
                    identity = min(
                        candidates[0],
                        key=lambda value: IdentifierKind.members().index(
                            value.kind
                        ),
                    )
                    identities.append(f"{identity.kind.value}:{identity.value}")
                    ready.append(index)
                # 索引保存请求标题与身份的关系；只有取得正文才会成为下一次完整缓存命中。
                title_entries.extend(
                    (titles[index], aliases) for aliases in candidates
                )

            if ready:
                remaining = (
                    max(0, deadline - asyncio.get_running_loop().time())
                    if deadline is not None
                    else None
                )
                try:
                    found = await self.lookup_service.lookup(
                        PaperLookup(identities, query.field),
                        credentials=credentials,
                        timeout=remaining,
                    )
                except (ProviderError, RedisError, TimeoutError) as error:
                    warning("Scholar matched paper lookup failed.", source="scholar", exc=error)
                    for index in ready:
                        resolved[index].status = MatchStatus.FAILED
                        resolved[index].limitations.append(ResultLimitation.from_error("scholar", error))
                else:
                    statuses = {
                        LookupStatus.FOUND: MatchStatus.FOUND,
                        LookupStatus.NOT_FOUND: MatchStatus.NOT_FOUND,
                        LookupStatus.AMBIGUOUS: MatchStatus.AMBIGUOUS,
                    }
                    for index, item in zip(ready, found.items, strict=True):
                        resolved[index] = MatchItem(
                            titles[index],
                            statuses.get(item.status, MatchStatus.FAILED),
                            item.paper,
                            item.limitations,
                        )

        # 内容解析全部完成后再写缓存，不能让可选写入耗尽后续查询的预算。
        if writes or title_entries:
            operation = "paper_cache.put_many" if writes else "paper_cache.index_titles"
            try:
                async with asyncio.timeout_at(deadline):
                    if writes:
                        await self.cache.put_many("arxiv", writes)
                    if title_entries:
                        operation = "paper_cache.index_titles"
                        await self.cache.index_titles(title_entries)
            except (RedisError, TimeoutError) as error:
                warning("Scholar cache write failed.", source="match", operation=operation,
                        error_type=type(error).__name__, error=str(error))

        # 把去重后的结果展开回原始标题位置（含重复项）。
        items = [MatchItem(title) for title in query.titles]
        for item, slots in zip(resolved, positions.values(), strict=True):
            for index in slots:
                items[index] = MatchItem(
                    query.titles[index],
                    item.status,
                    deepcopy(item.paper),
                    list(dict.fromkeys(item.limitations)),
                )
        return MatchResult(items)
