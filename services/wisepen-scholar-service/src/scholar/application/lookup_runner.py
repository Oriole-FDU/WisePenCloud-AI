"""search/lookup 共用的批量查询机制；来源缓存保存完整论文，出口裁剪留给用例。"""

from __future__ import annotations

import asyncio
import hashlib
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any

from redis.exceptions import RedisError

from scholar.domain.errors import ProviderBusy, ProviderError
from scholar.domain.interfaces.paper_lookup import PaperLookupProvider
from scholar.domain.models.identifiers import (
    IdentifierKind,
    PaperIdentifier,
    parse_identifier,
)
from scholar.domain.models.paper import Paper
from scholar.domain.models.source_execution import (
    SourceExecution,
    SourceExecutionStatus,
)
from scholar.domain.repositories.paper_lookup_cache import (
    CachedPaperLookup,
    PaperLookupCache,
)
from scholar.core.providers import ProviderPolicy
from scholar.core.providers.s2.transport import s2_queued_ahead


@dataclass
class LookupBinding:
    """来源声明支持的身份、有效信号与缓存规则；编排不读取供应商字段。"""

    provider: PaperLookupProvider
    cache: PaperLookupCache | None
    rules: ProviderPolicy


@dataclass
class LookupTarget:
    """一次用例内的查询目标；已有论文优先，可信身份用于跨来源接力。"""

    identifiers: list[PaperIdentifier]
    paper: Paper | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    observed_sources: set[str] = field(default_factory=set)
    general_found: bool = False
    ambiguous: bool = False
    errors: list[Exception] = field(default_factory=list)
    domain_errors: list[Exception] = field(default_factory=list)


@dataclass
class _SourceBatch:
    records: dict[PaperIdentifier, CachedPaperLookup] = field(default_factory=dict)
    errors: dict[PaperIdentifier, Exception] = field(default_factory=dict)
    ambiguous: set[PaperIdentifier] = field(default_factory=set)
    cache_hits: set[PaperIdentifier] = field(default_factory=set)
    cache_errors: list[Exception] = field(default_factory=list)
    queued_ahead: int | None = None


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
        positive_ttl_seconds: int = 86400,
        negative_ttl_seconds: int = 3600,
    ):
        self.bindings = bindings
        self.positive_ttl_seconds = positive_ttl_seconds
        self.negative_ttl_seconds = negative_ttl_seconds
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

    def _has_signals(self, source: str, metadata: dict) -> bool:
        rule = self.bindings[source].rules.has_signals
        return rule(metadata) if rule is not None else bool(metadata.get(source))

    async def run(
        self,
        targets: list[LookupTarget],
        *,
        common_sources: list[str],
        domain_sources: list[str],
        credentials: dict[str, str] | None = None,
    ) -> list[SourceExecution]:
        credentials = credentials or {}
        execution = []

        # 通用来源：任一命中即短路，未命中才继续下一个来源。
        for source in common_sources:
            # 入场前先根据已有 metadata 标记命中，避免重复调用。
            for target in targets:
                if self._has_signals(source, target.metadata):
                    target.general_found = True
            pending = [
                target
                for target in targets
                if not target.general_found and not target.ambiguous
            ]
            if not pending:
                break
            execution.append(
                await self._apply(source, pending, credentials.get(source))
            )
            for target in pending:
                if self._has_signals(source, target.metadata):
                    target.general_found = True

        # 通用来源互相替代，领域来源提供增量；前面的成功不能跳过领域来源。
        for source in domain_sources:
            before = [len(target.errors) for target in targets]
            execution.append(
                await self._apply(source, targets, credentials.get(source))
            )
            for target, offset in zip(targets, before, strict=True):
                target.domain_errors.extend(target.errors[offset:])
        return execution

    async def _apply(
        self,
        source: str,
        targets: list[LookupTarget],
        api_key: str | None,
    ) -> SourceExecution:
        binding = self.bindings[source]
        positions: dict[PaperIdentifier, list[LookupTarget]] = {}
        unsupported = False
        for target in targets:
            if target.ambiguous or source in target.observed_sources:
                continue
            known = target.metadata.get(source, {})
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
            if identifier is None:
                unsupported = True
            else:
                positions.setdefault(identifier, []).append(target)

        if not positions:
            return SourceExecution(
                source,
                False,
                SourceExecutionStatus.NOT_EXECUTED,
                "Source does not support the known paper identities"
                if unsupported
                else None,
            )

        known_ids = {
            identity: tuple(
                dict.fromkeys(
                    alias for target in entries for alias in target.identifiers
                )
            )
            for identity, entries in positions.items()
        }
        batch = await self._shared(source, known_ids, api_key)

        failures = 0
        first_error = None
        for identifier, entries in positions.items():
            error = batch.errors.get(identifier)
            if error is not None:
                if not isinstance(error, ProviderBusy):
                    failures += 1
                first_error = first_error or error
                for target in entries:
                    target.errors.append(error)
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
                # 命名空间整体替换，不融合不同来源的同名指标；基础信息取首个成功来源。
                target.metadata.update(deepcopy(record.paper.metadata))
                target.identifiers = list(
                    dict.fromkeys([*target.identifiers, *record.cache_aliases])
                )
                if target.paper is None:
                    target.paper = deepcopy(record.paper)
                target.paper.metadata = deepcopy(target.metadata)

        for error in batch.cache_errors:
            first_error = first_error or error
            for entries in positions.values():
                for target in entries:
                    target.errors.append(error)

        successes = len(batch.records) + len(batch.ambiguous)
        if successes and first_error:
            status = SourceExecutionStatus.PARTIAL
        elif successes:
            status = SourceExecutionStatus.COMPLETED
        elif failures:
            status = SourceExecutionStatus.FAILED
        else:
            status = SourceExecutionStatus.NOT_EXECUTED
        return SourceExecution(
            source,
            bool(batch.cache_hits),
            status,
            str(first_error) if first_error is not None else None,
            queued_ahead=batch.queued_ahead,
        )

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
    ) -> _SourceBatch:
        binding = self.bindings[source]
        batch = _SourceBatch()
        ids = list(known_ids)
        cached = [None] * len(ids)
        if binding.cache is not None:
            try:
                cached = await binding.cache.get_many(ids)
            except RedisError as error:
                batch.errors.update(dict.fromkeys(ids, error))
                return batch

        pending = []
        for identifier, record in zip(ids, cached, strict=True):
            if record is None:
                pending.append(identifier)
            elif identifier not in record.cache_aliases or (
                record.paper is not None
                and self._conflicts(
                    known_ids[identifier], self._aliases(record.paper)
                )
            ):
                batch.errors[identifier] = ProviderError(
                    "Cached lookup conflicts with the requested paper identity"
                )
            else:
                batch.records[identifier] = record
                batch.cache_hits.add(identifier)
        if not pending:
            return batch

        token = s2_queued_ahead.set(None)
        try:
            try:
                matches = await binding.provider.lookup(pending, api_key=api_key)
            except ProviderError as error:
                batch.errors.update(dict.fromkeys(pending, error))
                return batch
        finally:
            batch.queued_ahead = s2_queued_ahead.get()
            s2_queued_ahead.reset(token)

        writes = []
        for identifier, match in zip(pending, matches, strict=True):
            if match.error is not None:
                batch.errors[identifier] = ProviderError(match.error)
                continue
            if len(match.papers) > 1:
                # 歧义：不产生缓存别名，也不让后续来源基于任一候选继续关联。
                batch.ambiguous.add(identifier)
                continue
            paper = match.papers[0] if match.papers else None
            aliases = self._aliases(paper) if paper is not None else []
            if self._conflicts(known_ids[identifier], aliases):
                batch.errors[identifier] = ProviderError(
                    "Lookup conflicts with the requested paper identity"
                )
                continue
            record = CachedPaperLookup(
                paper, tuple(dict.fromkeys([identifier, *aliases]))
            )
            batch.records[identifier] = record
            short = paper is None or (
                binding.rules.short_lived is not None
                and binding.rules.short_lived(record)
            )
            writes.append(
                (
                    record,
                    self.negative_ttl_seconds if short else self.positive_ttl_seconds,
                )
            )
        if binding.cache is not None and writes:
            try:
                await binding.cache.put_many(writes)
            except RedisError as error:
                batch.cache_errors.append(error)
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

    @staticmethod
    def _conflicts(
        known: tuple[PaperIdentifier, ...],
        actual: list[PaperIdentifier],
    ) -> bool:
        return any(
            left.kind == right.kind and left.value != right.value
            for left in known
            for right in actual
        )
