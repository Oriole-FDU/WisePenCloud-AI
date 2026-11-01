"""批量完整标题解析：共享身份索引 → 本地标题索引 → Crossref → 既有 lookup。"""

from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import dataclass
from enum import StrEnum

from redis.exceptions import RedisError

from scholar.application.lookup import LookupStatus, PaperLookup, PaperLookupService
from scholar.core.providers.policies import paper_identifiers
from scholar.domain.errors import ProviderError
from scholar.domain.interfaces.title_resolver import TitleResolver
from scholar.domain.models.fields import AcademicField
from scholar.domain.models.identifiers import IdentifierKind
from scholar.domain.models.paper import Paper
from scholar.domain.models.source_execution import (
    SourceExecution,
    SourceExecutionStatus,
)
from scholar.domain.models.titles import (
    TitleMatch,
    merge_title_identities,
    normalize_title,
)
from scholar.domain.repositories.local_paper_repository import LocalPaperRepository
from scholar.domain.repositories.title_cache import TitleCache


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
    error: str | None = None


@dataclass
class MatchResult:
    """保留标题顺序、重复项及逐项失败的公共响应。"""

    items: list[MatchItem]
    execution: list[SourceExecution]


def _error_text(error: BaseException) -> str:
    """异常可能没有文本（如某些自定义异常），用类名兜底。"""
    return str(error) or type(error).__name__


def _execution(
    source: str,
    matches: list[TitleMatch],
    *,
    cache_hit: bool = False,
) -> SourceExecution:
    """按成功/失败比例汇总一次来源执行的 SourceExecution。"""
    failures = [match.error for match in matches if match.error]
    if not failures:
        status = SourceExecutionStatus.COMPLETED
    elif len(failures) == len(matches):
        status = SourceExecutionStatus.FAILED
    else:
        status = SourceExecutionStatus.PARTIAL
    return SourceExecution(source, cache_hit, status, failures[0] if failures else None)


class PaperMatchService:
    """只编排标题到稳定身份的解析；论文获取、富化和投影全部委托 lookup。"""

    def __init__(
        self,
        cache: TitleCache,
        local: LocalPaperRepository,
        crossref: TitleResolver,
        lookup: PaperLookupService,
    ):
        self.cache, self.local, self.crossref, self.lookup_service = (
            cache,
            local,
            crossref,
            lookup,
        )

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

        # 标题归一化后去重：positions 记录每个唯一标题对应的所有原始位置。
        items = [MatchItem(title) for title in query.titles]
        positions: dict[str, list[int]] = {}
        for index, title in enumerate(query.titles):
            positions.setdefault(normalize_title(title), []).append(index)
        titles = [items[indexes[0]].title for indexes in positions.values()]
        slots = list(positions.values())

        execution: list[SourceExecution] = []
        matches = [TitleMatch() for _ in titles]

        # 1) 共享标题缓存：命中则得到候选身份，未命中留空继续后续阶段。
        budget = asyncio.timeout_at(deadline)
        try:
            async with budget:
                matches = await self.cache.get_many(titles)
            execution.append(
                _execution(
                    "title_cache",
                    matches,
                    cache_hit=any(m.candidates for m in matches),
                )
            )
        except (RedisError, TimeoutError) as error:
            # 缓存失败不影响后续阶段；本地索引仍可提供匹配。
            execution.append(
                SourceExecution(
                    "title_cache",
                    False,
                    SourceExecutionStatus.FAILED,
                    _error_text(error),
                )
            )

        # 2) 本地标题索引：仅处理缓存无候选、且无错误的项。
        pending = [
            i
            for i, match in enumerate(matches)
            if not match.candidates and not match.error
        ]
        if pending:
            budget = asyncio.timeout_at(deadline)
            try:
                async with budget:
                    local = await self.local.match_titles([titles[i] for i in pending])
                # 把本地多条论文的身份合并为一个候选集合。
                resolved = [
                    TitleMatch(
                        merge_title_identities(
                            [paper_identifiers(paper) for paper in match.papers]
                        ),
                        match.error,
                    )
                    for match in local
                ]
            except (ProviderError, TimeoutError) as error:
                resolved = [
                    TitleMatch(error=_error_text(error)) for _ in pending
                ]
            execution.append(_execution("arxiv", resolved))
            for index, match in zip(pending, resolved, strict=True):
                matches[index] = match

        # 3) 已解决项与慢速 Crossref 并行 lookup；先启动避免被后者耗尽预算。
        ready = [i for i, match in enumerate(matches) if match.candidates]
        tasks = (
            [
                asyncio.create_task(
                    self._lookup(
                        ready, titles, matches, query.field, credentials, deadline
                    )
                )
            ]
            if ready
            else []
        )
        try:
            pending = [i for i, match in enumerate(matches) if not match.candidates]
            if pending:
                resolved = await self.crossref.match(
                    [titles[i] for i in pending], deadline=deadline
                )
                execution.append(_execution("crossref", resolved))
                for index, match in zip(pending, resolved, strict=True):
                    matches[index] = match
                ready = [i for i in pending if matches[i].candidates]
                if ready:
                    tasks.append(
                        asyncio.create_task(
                            self._lookup(
                                ready,
                                titles,
                                matches,
                                query.field,
                                credentials,
                                deadline,
                            )
                        )
                    )

            # 汇总 lookup 结果到原始位置（同一标题可能对应多个原始位置）。
            for result, events in await asyncio.gather(*tasks):
                for index, item in result.items():
                    for position in slots[index]:
                        items[position] = MatchItem(
                            items[position].title,
                            item.status,
                            deepcopy(item.paper),
                            item.error,
                        )
                execution.extend(events)
        finally:
            # 任一 lookup 失败或调用者取消时，其余任务不得游离在用例之外。
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

        # 未解决项：有 error 记 FAILED，否则记 NOT_FOUND。
        for index, match in enumerate(matches):
            if not match.candidates:
                for position in slots[index]:
                    items[position].status = (
                        MatchStatus.FAILED if match.error else MatchStatus.NOT_FOUND
                    )
                    items[position].error = match.error
        return MatchResult(items, execution)

    async def _lookup(
        self,
        indexes,
        titles,
        matches,
        field,
        credentials,
        deadline,
    ):
        result = {}
        events = []
        unique = []
        identities = []
        for index in indexes:
            candidates = merge_title_identities(matches[index].candidates)
            if len(candidates) > 1:
                result[index] = MatchItem("", MatchStatus.AMBIGUOUS)
            else:
                # 同一论文优先使用 arXiv，其次 DOI，再使用其他稳定身份。
                identity = min(
                    candidates[0],
                    key=lambda value: IdentifierKind.members().index(value.kind),
                )
                identities.append(
                    f"{identity.kind.value if identity.kind != IdentifierKind.CORPUS_ID else 'corpusid'}:"
                    f"{identity.value}"
                )
                unique.append(index)

        # 保留请求标题到解析身份的关联，即使详情来源使用了另一种正式标题。
        try:
            async with asyncio.timeout_at(deadline):
                await self.cache.put_many(
                    [
                        (titles[index], aliases)
                        for index in indexes
                        for aliases in matches[index].candidates
                    ]
                )
        except (RedisError, TimeoutError) as error:
            events.append(
                SourceExecution(
                    "title_cache",
                    False,
                    SourceExecutionStatus.FAILED,
                    _error_text(error),
                )
            )

        if not unique:
            return result, events

        # 剩余预算 = deadline - 当前时间；无 deadline 则不限时。
        remaining = (
            max(0, deadline - asyncio.get_running_loop().time())
            if deadline is not None
            else None
        )
        try:
            found = await self.lookup_service.lookup(
                PaperLookup(identities, field),
                credentials=credentials,
                timeout=remaining,
            )
        except (ProviderError, RedisError, TimeoutError) as error:
            for index in unique:
                result[index] = MatchItem(
                    "", MatchStatus.FAILED, error=_error_text(error)
                )
            return result, [
                *events,
                SourceExecution(
                    "lookup",
                    False,
                    SourceExecutionStatus.FAILED,
                    _error_text(error),
                ),
            ]

        statuses = {
            LookupStatus.FOUND: MatchStatus.FOUND,
            LookupStatus.NOT_FOUND: MatchStatus.NOT_FOUND,
            LookupStatus.AMBIGUOUS: MatchStatus.AMBIGUOUS,
        }
        for index, item in zip(unique, found.items, strict=True):
            result[index] = MatchItem(
                "",
                statuses.get(item.status, MatchStatus.FAILED),
                item.paper,
                item.error,
            )
        return result, [*events, *found.execution]
