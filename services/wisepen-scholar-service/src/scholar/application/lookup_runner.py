"""search/lookup 共用的批量查询机制；来源缓存保存完整论文，出口裁剪留给用例。"""

from __future__ import annotations

import asyncio
import hashlib
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any

from redis.exceptions import RedisError
from common.logger import warning

from scholar.domain.errors import ProviderError
from scholar.domain.interfaces.paper_lookup import PaperLookupProvider
from scholar.domain.models.identifiers import (
    IdentifierKind,
    PaperIdentifier,
    parse_identifier,
    identities_conflict,
)
from scholar.domain.models.paper import Paper
from scholar.domain.repositories.paper_cache import (
    CachedPaperLookup,
    PaperCache,
)
from scholar.core.providers import ProviderPolicy


@dataclass
class LookupBinding:
    """来源声明支持的身份、详情完整性与缓存；编排不读取供应商字段。"""

    provider: PaperLookupProvider
    cache: PaperCache | None
    rules: ProviderPolicy


@dataclass
class LookupTarget:
    """一次用例内的查询目标；已有论文优先，可信身份用于跨来源接力。"""

    identifiers: list[PaperIdentifier]
    paper: Paper | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    observed_sources: set[str] = field(default_factory=set)
    cache_hit: bool = False
    general_found: bool = False
    ambiguous: bool = False
    errors: dict[str, Exception] = field(default_factory=dict)


@dataclass
class _SourceBatch:
    records: dict[PaperIdentifier, CachedPaperLookup] = field(default_factory=dict)
    errors: dict[PaperIdentifier, Exception] = field(default_factory=dict)
    ambiguous: set[PaperIdentifier] = field(default_factory=set)
    cache_hits: set[PaperIdentifier] = field(default_factory=set)
    cache_writes: list[tuple[CachedPaperLookup, int]] = field(default_factory=list)


@dataclass
class _InFlight:
    task: asyncio.Task[_SourceBatch]
    waiters: int = 0


class LookupRunner:
    """按来源批量回退，并在 search/lookup 之间共享缓存和在途 I/O。"""

    def __init__(
        self,
        bindings: dict[str, LookupBinding],
        *,
        ttl_seconds: int = 86400,
    ):
        self.bindings = bindings
        self.ttl_seconds = ttl_seconds
        self._inflight: dict[tuple, _InFlight] = {}
        self._lock = asyncio.Lock()

    def target(
        self,
        identifier: PaperIdentifier | None,
        paper: Paper | None = None,
    ) -> LookupTarget:
        """构造查询目标；已有 paper 时合并其身份别名。"""
        identifiers = [] if identifier is None else [identifier]
        metadata = deepcopy(paper.metadata) if paper is not None else {}
        if paper is not None:
            identifiers.extend(self._aliases(paper))
        return LookupTarget(
            list(dict.fromkeys(identifiers)), deepcopy(paper), metadata
        )

    async def cache_papers(self, source: str, papers: list[Paper]) -> None:
        """搜索的完整来源事实写入既有 lookup 缓存；列表概要仅建立标题身份索引。"""
        binding = self.bindings.get(source)
        if binding is None or binding.cache is None:
            return
        records = [
            (CachedPaperLookup(paper, tuple(self._aliases(paper))), self.ttl_seconds)
            for paper in papers
            if all(name in paper.metadata.get(source, {}) for name in binding.rules.required_fields)
        ]
        if records:
            await binding.cache.put_many(source, records)

    async def run(
        self,
        targets: list[LookupTarget],
        *,
        common_sources: list[str],
        domain_sources: list[str],
        credentials: dict[str, str] | None = None,
        deadline: float | None = None,
    ) -> None:
        credentials = credentials or {}

        # 已由通用来源取得的论文无需补信号；arXiv 的 S2 事实由离线同步负责。
        for target in targets:
            if target.paper is not None and target.paper.source in common_sources:
                target.general_found = True

        # 先读完各来源缓存，后序命中不能被前序网络请求挡住。
        for source in dict.fromkeys([*common_sources, *domain_sources]):
            await self._apply(
                source, [target for target in targets if not target.general_found] if source in common_sources else targets,
                credentials.get(source), deadline,
                resolve_common=source in common_sources, cache_only=True,
            )
        for target in targets:
            if target.cache_hit:
                target.general_found = True

        # 通用来源只负责精确获取；命中论文即停止，与引用数、TLDR 是否齐全无关。
        # 隐含前提：通用源的 search 结果与 lookup 结果一致，均包含完整的富化信息，只要命中了缓存就已经完整
        for source in common_sources:
            pending = [
                target
                for target in targets
                if not target.general_found and not target.ambiguous
            ]
            if not pending:
                break
            await self._apply(
                source, pending, credentials.get(source), deadline,
                resolve_common=True,
            )

        # 领域来源提供增量，失败只审计；仍保留身份歧义，不能任选候选。
        for source in domain_sources:
            await self._apply(
                source, [target for target in targets if not target.cache_hit],
                credentials.get(source), deadline,
            )

    async def _apply(
        self,
        source: str,
        targets: list[LookupTarget],
        api_key: str | None,
        deadline: float | None,
        *,
        resolve_common: bool = False,
        cache_only: bool = False,
    ) -> None:
        binding = self.bindings[source]
        positions: dict[PaperIdentifier, list[LookupTarget]] = {}
        for target in targets:
            if target.ambiguous or source in target.observed_sources:
                continue
            known = target.metadata.get(source, {})
            # 无详情差异的领域来源已在搜索中取得时，不重复查询同源；
            # HF 等列表概要仍由 required_fields 决定是否需要获取详情。
            if not resolve_common and not binding.rules.required_fields and (
                source in target.metadata
                or (target.paper is not None and target.paper.source == source)
            ):
                continue
            # 必填字段齐全即代表已具备完整详情（即使值为 None 也算已确认事实），
            # 跳过冗余请求。
            if binding.rules.required_fields and all(
                name in known for name in binding.rules.required_fields
            ):
                continue
            # 按 binding 声明的 kind 顺序，挑首个可用身份。
            identifier = next(
                (
                    identity
                    for kind in IdentifierKind.members()
                    for identity in target.identifiers
                    if identity.kind == kind
                    and kind in binding.rules.identifier_kinds
                ),
                None,
            )
            if identifier is not None:
                positions.setdefault(identifier, []).append(target)

        if not positions:
            return

        known_ids = {
            identity: tuple(
                dict.fromkeys(
                    alias for target in entries for alias in target.identifiers
                )
            )
            for identity, entries in positions.items()
        }
        budget = asyncio.timeout_at(deadline)
        try:
            async with budget:
                if cache_only:
                    batch = await self._load(source, known_ids, api_key, cache_only=True)
                else:
                    batch = await self._shared(source, known_ids, api_key)
        except TimeoutError as error:
            if not budget.expired():
                raise
            # 只隔离本次用例持有的超时；共享任务仍由其他等待者继续使用。
            failure = ProviderError(f"{source} lookup exceeded the request deadline")
            # 整批读取失败时，HTTP 仍需通过原始原因识别 504。
            failure.__cause__ = error
            batch = _SourceBatch(errors=dict.fromkeys(positions, failure))

        # 这里只消费已知外部失败；错误按来源保留，最终论文成功即可消除公开限制。
        for error in dict.fromkeys(batch.errors.values()):
            warning("Scholar lookup source failed.", source=source,
                    operation="paper_cache.get_many" if cache_only else "lookup",
                    exc=error, cause=error.__cause__)
        for identifier, entries in positions.items():
            error = batch.errors.get(identifier)
            if error is not None:
                for target in entries:
                    target.errors[source] = error
                    if cache_only:
                        target.observed_sources.add(source)
            if identifier in batch.ambiguous:
                for target in entries:
                    target.ambiguous = True
            record = batch.records.get(identifier)
            if record is None:
                continue
            for target in entries:
                target.observed_sources.add(source)
                if record.paper is None:
                    continue
                if identifier in batch.cache_hits and target.paper is None:
                    target.cache_hit = True
                if resolve_common:
                    target.general_found = True
                # 命名空间整体替换，不融合不同来源的同名指标；基础信息取首个成功来源。
                target.metadata.update(deepcopy(record.paper.metadata))
                target.identifiers = list(
                    dict.fromkeys([*target.identifiers, *record.cache_aliases])
                )
                if target.paper is None:
                    target.paper = deepcopy(record.paper)
                target.paper.metadata = deepcopy(target.metadata)

        if binding.cache is not None and batch.cache_writes:
            # 共享批次的待写条目只取走一次；读取结果已交给各等待者，写入不再决定其成败。
            writes, batch.cache_writes = batch.cache_writes, []
            try:
                async with asyncio.timeout_at(deadline):
                    await binding.cache.put_many(source, writes)
            except (RedisError, TimeoutError) as error:
                warning("Scholar cache write failed.", source=source, operation="paper_cache.put_many",
                        error_type=type(error).__name__, error=str(error))

    async def _shared(
        self,
        source: str,
        known_ids: dict[PaperIdentifier, tuple[PaperIdentifier, ...]],
        api_key: str | None,
    ) -> _SourceBatch:
        """相同来源、凭证与身份共享在途 I/O；持久缓存仍按论文事实共享。"""
        identities = tuple(
            sorted(
                (
                    (
                        identifier,
                        tuple(
                            sorted(
                                aliases,
                                key=lambda item: (item.kind, item.value),
                            )
                        ),
                    )
                    for identifier, aliases in known_ids.items()
                ),
                key=lambda entry: (entry[0].kind, entry[0].value),
            )
        )
        fingerprint = hashlib.sha256(api_key.encode()).hexdigest() if api_key else None
        key = (source, fingerprint, identities)
        async with self._lock:
            entry = self._inflight.get(key)
            if entry is None:
                task = asyncio.create_task(
                    self._load(source, known_ids, api_key)
                )
                task.add_done_callback(self._consume_exception)
                entry = self._inflight[key] = _InFlight(task)
            entry.waiters += 1
        try:
            return await asyncio.shield(entry.task)
        finally:
            async with self._lock:
                entry.waiters -= 1
                if entry.waiters == 0:
                    self._inflight.pop(key)
                    if not entry.task.done():
                        entry.task.cancel()

    @staticmethod
    def _consume_exception(task: asyncio.Task) -> None:
        if not task.cancelled():
            # await 仍向等待者传播异常；这里只回收取消竞争时可能无人接收的任务异常。
            task.exception()

    async def _load(
        self,
        source: str,
        known_ids: dict[PaperIdentifier, tuple[PaperIdentifier, ...]],
        api_key: str | None,
        *,
        cache_only: bool = False,
    ) -> _SourceBatch:
        binding = self.bindings[source]
        batch = _SourceBatch()
        ids = list(known_ids)
        cached = [None] * len(ids)
        # 共享任务入场前可能已有另一请求填好缓存，入场后仍须复用其结果。
        if binding.cache is not None:
            try:
                cached = await binding.cache.get_many(source, ids)
            except RedisError as error:
                batch.errors.update(dict.fromkeys(ids, error))
                return batch

        pending = []
        for identifier, record in zip(ids, cached, strict=True):
            if record is None:
                pending.append(identifier)
            elif identifier not in record.cache_aliases or (
                record.paper is not None
                and identities_conflict(
                    known_ids[identifier], self._aliases(record.paper)
                )
            ):
                batch.errors[identifier] = ProviderError(
                    "Cached lookup conflicts with the requested paper identity"
                )
            else:
                batch.records[identifier] = record
                batch.cache_hits.add(identifier)
        if cache_only or not pending:
            return batch

        try:
            matches = await binding.provider.lookup(pending, api_key=api_key)
        except ProviderError as error:
            batch.errors.update(dict.fromkeys(pending, error))
            return batch

        for identifier, match in zip(pending, matches, strict=True):
            if match.error is not None:
                batch.errors[identifier] = match.error
                continue
            if len(match.papers) > 1:
                # 歧义：不产生缓存别名，也不让后续来源基于任一候选继续关联。
                batch.ambiguous.add(identifier)
                continue
            paper = match.papers[0] if match.papers else None
            aliases = self._aliases(paper) if paper is not None else []
            if identities_conflict(known_ids[identifier], aliases):
                batch.errors[identifier] = ProviderError(
                    "Lookup conflicts with the requested paper identity"
                )
                continue
            record = CachedPaperLookup(
                paper, tuple(dict.fromkeys([identifier, *aliases]))
            )
            batch.records[identifier] = record
            # 未找到也是已完成的查询结果，与找到论文共用完整缓存周期。
            if binding.cache is not None:
                batch.cache_writes.append((record, self.ttl_seconds))
        return batch

    def _aliases(self, paper: Paper) -> list[PaperIdentifier]:
        aliases = []
        if paper.arxiv_id is not None:
            aliases.append(parse_identifier(paper.arxiv_id))
        if paper.doi is not None:
            aliases.append(parse_identifier(paper.doi))
        for binding in self.bindings.values():
            if binding.rules.aliases is not None:
                aliases.extend(binding.rules.aliases(paper.metadata))
        return list(dict.fromkeys(aliases))
