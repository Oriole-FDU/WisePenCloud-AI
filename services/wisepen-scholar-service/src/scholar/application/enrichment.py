"""search/lookup 共用的来源执行机制；通用来源择一，领域来源只追加增量事实。"""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass, field, replace
from typing import Any

from redis.exceptions import RedisError

from scholar.application.source_execution import SourceExecution, SourceExecutionStatus
from scholar.domain.interfaces.paper_enricher import PaperEnricher, PaperEnrichment
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
    """来源自述身份、信号和缓存规则，编排层不读取供应商专有字段。"""

    enricher: PaperEnricher | None
    cache: PaperEnrichmentCache | None
    identifier_kinds: list[IdentifierKind]
    coverage: str = ""
    fields: list[str] = field(default_factory=list)
    has_signals: Callable[[dict[str, Any]], bool] | None = None
    aliases: Callable[[dict[str, Any]], list[PaperIdentifier]] | None = None
    short_lived: Callable[[CachedPaperEnrichment], bool] | None = None
    requires_api_key: bool = False


@dataclass
class EnrichmentTarget:
    """一次用例内的论文状态；identifiers 保留可信身份，错误保留原始异常。"""

    identifiers: list[PaperIdentifier]
    paper: Paper | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    observed_sources: set[str] = field(default_factory=set)
    general_found: bool = False
    domain_found: bool = False
    errors: list[Exception] = field(default_factory=list)
    domain_errors: list[Exception] = field(default_factory=list)

    @property
    def has_signals(self) -> bool:
        return self.general_found or self.domain_found


@dataclass
class _SourceBatch:
    records: dict[PaperIdentifier, CachedPaperEnrichment] = field(default_factory=dict)
    errors: dict[PaperIdentifier, Exception] = field(default_factory=dict)
    cache_hits: set[PaperIdentifier] = field(default_factory=set)
    cache_errors: list[Exception] = field(default_factory=list)


@dataclass
class _InFlight:
    task: asyncio.Task[_SourceBatch]
    waiters: int = 0


class EnrichmentRunner:
    """应用内部机制，共享缓存和在途 I/O；不构成第四个应用用例。"""

    def __init__(
        self,
        bindings: dict[str, EnrichmentBinding],
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
        self, identifier: PaperIdentifier | None, paper: Paper | None = None
    ) -> EnrichmentTarget:
        metadata = deepcopy(paper.metadata) if paper is not None else {}
        identifiers = [] if identifier is None else [identifier]
        if paper is not None:
            if paper.arxiv_id is not None:
                identifiers.append(parse_identifier(paper.arxiv_id))
            if paper.doi is not None:
                identifiers.append(parse_identifier(paper.doi))
        for binding in self.bindings.values():
            if binding.aliases is not None:
                identifiers.extend(binding.aliases(metadata))
        return EnrichmentTarget(
            list(dict.fromkeys(identifiers)), deepcopy(paper), metadata
        )

    def _has_signals(self, source: str, metadata: dict) -> bool:
        rule = self.bindings[source].has_signals
        return rule(metadata) if rule is not None else bool(metadata.get(source))

    async def run(
        self,
        targets: list[EnrichmentTarget],
        *,
        common_sources: list[str],
        domain_sources: list[str],
        credentials: dict[str, str] | None = None,
        full: bool = False,
        cache_only: bool = False,
    ) -> list[SourceExecution]:
        credentials = credentials or {}
        execution = []
        for source in common_sources:
            for target in targets:
                if self._has_signals(source, target.metadata):
                    target.general_found = True
            pending = [target for target in targets if not target.general_found]
            if not pending:
                break
            if any(source not in target.observed_sources for target in pending):
                execution.append(
                    await self._apply(
                        source, pending, credentials.get(source), full, cache_only
                    )
                )
            for target in pending:
                if self._has_signals(source, target.metadata):
                    target.general_found = True

        # 平台不融合同名字段、不做语义去重；领域 provider 负责键唯一和增量价值。
        # 一个领域来源失败不能阻止其他来源；通用成功也不能让领域增量提前结束。
        for source in domain_sources:
            before = [len(target.errors) for target in targets]
            execution.append(
                await self._apply(
                    source, targets, credentials.get(source), full, cache_only
                )
            )
            for target, offset in zip(targets, before, strict=True):
                target.domain_errors.extend(target.errors[offset:])
                if self._has_signals(source, target.metadata):
                    target.domain_found = True
        return execution

    async def _apply(
        self,
        source: str,
        targets: list[EnrichmentTarget],
        api_key: str | None,
        full: bool,
        cache_only: bool,
    ) -> SourceExecution:
        binding = self.bindings[source]
        groups: dict[bool, dict[PaperIdentifier, list[EnrichmentTarget]]] = {
            False: {},
            True: {},
        }
        unsupported = False
        for target in targets:
            if source in target.observed_sources:
                continue
            known = target.metadata.get(source, {})
            if (
                not cache_only
                and binding.fields
                and all(name in known for name in binding.fields)
            ):
                continue
            identifier = next(
                (
                    identity
                    for kind in (
                        IdentifierKind.ARXIV,
                        IdentifierKind.DOI,
                        IdentifierKind.OPENALEX,
                        IdentifierKind.S2,
                        IdentifierKind.CORPUS_ID,
                    )
                    for identity in target.identifiers
                    if identity.kind == kind and kind in binding.identifier_kinds
                ),
                None,
            )
            if identifier is not None:
                groups[full and target.paper is None].setdefault(identifier, []).append(
                    target
                )
            else:
                unsupported = True

        successes = failures = 0
        cache_hits = False
        first_error: Exception | None = None
        for include_details, positions in groups.items():
            if not positions:
                continue
            known_ids = {
                identity: tuple(
                    dict.fromkeys(
                        alias for target in entries for alias in target.identifiers
                    )
                )
                for identity, entries in positions.items()
            }
            batch = await self._shared(
                source, known_ids, include_details, cache_only, api_key
            )
            cache_hits = cache_hits or bool(batch.cache_hits)
            for identifier, entries in positions.items():
                error = batch.errors.get(identifier)
                if error is not None:
                    failures += 1
                    first_error = first_error or error
                    for target in entries:
                        target.errors.append(error)
                    continue
                record = batch.records.get(identifier)
                if record is None:
                    continue
                successes += 1
                for target in entries:
                    target.observed_sources.add(source)
                    enrichment = record.enrichment
                    if enrichment is None:
                        continue
                    # 来源命名空间是完整投影；不递归融合内部同名字段或跨来源指标。
                    target.metadata.update(deepcopy(enrichment.metadata))
                    target.identifiers = list(
                        dict.fromkeys([*target.identifiers, *enrichment.identifiers])
                    )
                    if target.paper is None and enrichment.paper is not None:
                        target.paper = deepcopy(enrichment.paper)
                    if target.paper is not None:
                        target.paper.metadata = deepcopy(target.metadata)
            if batch.cache_errors:
                first_error = first_error or batch.cache_errors[0]
                for entries in positions.values():
                    for target in entries:
                        target.errors.extend(batch.cache_errors)

        status = (
            SourceExecutionStatus.PARTIAL
            if successes
            and (
                first_error or successes < sum(len(group) for group in groups.values())
            )
            else SourceExecutionStatus.FAILED
            if failures
            else SourceExecutionStatus.COMPLETED
            if successes
            else SourceExecutionStatus.NOT_EXECUTED
        )
        return SourceExecution(
            source,
            cache_hits,
            binding.coverage,
            status,
            str(first_error)
            if first_error is not None
            else "Source does not support the known paper identities"
            if unsupported and not any(groups.values())
            else None,
        )

    async def _shared(
        self,
        source: str,
        known_ids: dict[PaperIdentifier, tuple[PaperIdentifier, ...]],
        include_details: bool,
        cache_only: bool,
        api_key: str | None,
    ) -> _SourceBatch:
        fingerprint = (
            None if api_key is None else hashlib.sha256(api_key.encode()).hexdigest()
        )
        identities = tuple(
            sorted(
                [
                    (
                        identifier,
                        tuple(
                            sorted(aliases, key=lambda alias: (alias.kind, alias.value))
                        ),
                    )
                    for identifier, aliases in known_ids.items()
                ],
                key=lambda entry: (entry[0].kind, entry[0].value),
            )
        )
        key = (source, include_details, cache_only, fingerprint, identities)
        async with self._lock:
            entry = self._inflight.get(key)
            if entry is None:
                task = asyncio.create_task(
                    self._load(source, known_ids, include_details, cache_only, api_key)
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
            task.exception()

    async def _load(
        self,
        source: str,
        known_ids: dict[PaperIdentifier, tuple[PaperIdentifier, ...]],
        include_details: bool,
        cache_only: bool,
        api_key: str | None,
    ) -> _SourceBatch:
        binding = self.bindings[source]
        batch = _SourceBatch()
        # BYOK 的缓存和在途请求都按凭证隔离；缺 key 不能借他人的缓存绕过来源契约。
        if binding.requires_api_key and not api_key:
            if cache_only:
                return batch
            batch.errors.update(
                dict.fromkeys(known_ids, ProviderError(f"{source} API key is required"))
            )
            return batch
        cache_options = (
            {"scope": hashlib.sha256(api_key.encode()).hexdigest()}
            if binding.requires_api_key
            else {}
        )
        pending = []
        for identifier, aliases in known_ids.items():
            cached = None
            if binding.cache is not None:
                try:
                    cached = await binding.cache.get([identifier], **cache_options)
                except RedisError as error:
                    batch.errors[identifier] = error
                    continue
            if cached is not None:
                if identifier not in cached.cache_aliases or (
                    cached.enrichment is not None
                    and self._conflicts(
                        aliases, self._aliases(binding, cached.enrichment)
                    )
                ):
                    batch.errors[identifier] = ProviderError(
                        "Cached enrichment conflicts with the requested paper identity"
                    )
                    continue
                batch.cache_hits.add(identifier)
                batch.records[identifier] = cached
            if cache_only or binding.enricher is None:
                continue
            if cached is not None and (
                not cached.record_found
                or not include_details
                or cached.details_confirmed
            ):
                continue
            pending.append(identifier)

        if not pending:
            return batch
        try:
            responses = await binding.enricher.enrich(
                pending, include_details=include_details, api_key=api_key
            )
        except ProviderError as error:
            batch.errors.update(dict.fromkeys(pending, error))
            return batch
        for identifier, response in zip(pending, responses, strict=True):
            if response is not None:
                aliases = self._aliases(binding, response)
                if self._conflicts(known_ids[identifier], aliases):
                    batch.errors[identifier] = ProviderError(
                        "Enrichment conflicts with the requested paper identity"
                    )
                    continue
                response = replace(
                    response, identifiers=list(dict.fromkeys([identifier, *aliases]))
                )
            previous = batch.records.get(identifier)
            if response is None and previous is not None and previous.record_found:
                # 详情补查未找到时保留已取得的信号，但确认本次没有基础详情。
                response = previous.enrichment
            aliases = tuple(
                dict.fromkeys(
                    [
                        identifier,
                        *(response.identifiers if response is not None else []),
                    ]
                )
            )
            record = CachedPaperEnrichment(
                response, aliases, response is not None, include_details
            )
            batch.records[identifier] = record
            if binding.cache is not None:
                short = not record.record_found or (
                    binding.short_lived is not None and binding.short_lived(record)
                )
                try:
                    await binding.cache.put(
                        list(aliases),
                        record,
                        **cache_options,
                        ttl_seconds=self.negative_ttl_seconds
                        if short
                        else self.positive_ttl_seconds,
                    )
                except RedisError as error:
                    batch.cache_errors.append(error)
        return batch

    @staticmethod
    def _aliases(
        binding: EnrichmentBinding, enrichment: PaperEnrichment
    ) -> list[PaperIdentifier]:
        aliases = list(enrichment.identifiers)
        if binding.aliases is not None:
            aliases.extend(binding.aliases(enrichment.metadata))
        if enrichment.paper is not None:
            if enrichment.paper.arxiv_id is not None:
                aliases.append(parse_identifier(enrichment.paper.arxiv_id))
            if enrichment.paper.doi is not None:
                aliases.append(parse_identifier(enrichment.paper.doi))
        return list(dict.fromkeys(aliases))

    @staticmethod
    def _conflicts(
        known: tuple[PaperIdentifier, ...], actual: list[PaperIdentifier]
    ) -> bool:
        return any(
            left.kind == right.kind and left.value != right.value
            for left in known
            for right in actual
        )
