"""lookup 用例内的 S2 缓存读取、在线富化与事实合并。"""

import asyncio
from copy import deepcopy
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from common.core.exceptions import ServiceException
from redis.exceptions import RedisError

from scholar.application.source_execution import SourceExecution, SourceExecutionStatus
from scholar.domain.error_codes import ScholarErrorCode
from scholar.domain.interfaces.paper_enricher import PaperEnricher, PaperEnrichment
from scholar.domain.interfaces.provider_error import ProviderError
from scholar.domain.models.identifiers import (
    PaperIdentifier,
    parse_identifier,
)
from scholar.domain.models.paper import Paper
from scholar.domain.repositories.paper_enrichment_cache import (
    CachedPaperEnrichment,
    PaperEnrichmentCache,
)

POSITIVE_TTL_SECONDS = 24 * 60 * 60
NEGATIVE_TTL_SECONDS = 60 * 60


class EnrichmentStatus(StrEnum):
    COMPLETE = "complete"
    NOT_FOUND = "not_found"
    FAILED = "failed"
    UNSUPPORTED = "unsupported"


@dataclass
class EnrichmentTarget:
    identifier: PaperIdentifier
    paper: Paper | None


@dataclass
class _Outcome:
    status: EnrichmentStatus
    paper: Paper | None
    metadata: dict[str, Any] | None
    error: Exception | None = None
    cache_hit: bool = False


@dataclass
class _TargetGroup:
    identifier: PaperIdentifier
    targets: list[EnrichmentTarget]
    needs_core: bool
    needs_details: bool


@dataclass
class _Batch:
    records: dict[PaperIdentifier, CachedPaperEnrichment]
    errors: dict[PaperIdentifier, Exception]
    cache_hits: set[PaperIdentifier]
    provider_attempted: set[PaperIdentifier]
    provider_succeeded: set[PaperIdentifier]
    cache_write_errors: list[Exception]


@dataclass
class _InFlightBatch:
    task: asyncio.Task[_Batch]
    waiters: int = 0


class _S2Enrichment:
    """在 lookup 生命周期内协调共享缓存、在线批次和取消。"""

    def __init__(
        self,
        enricher: PaperEnricher | None,
        cache: PaperEnrichmentCache | None,
        *,
        positive_ttl_seconds: int = POSITIVE_TTL_SECONDS,
        negative_ttl_seconds: int = NEGATIVE_TTL_SECONDS,
    ):
        self.enricher = enricher
        self.cache = cache
        self.positive_ttl_seconds = positive_ttl_seconds
        self.negative_ttl_seconds = negative_ttl_seconds
        self._inflight: dict[tuple, _InFlightBatch] = {}
        self._inflight_lock = asyncio.Lock()

    async def resolve(
        self,
        targets: list[EnrichmentTarget],
        *,
        full: bool,
        cache_only: bool = False,
    ) -> tuple[dict[PaperIdentifier, _Outcome], SourceExecution]:
        groups = self._group(targets, full)
        key = self._batch_key(groups, full, cache_only)
        batch = await self._shared_batch(key, groups, full, cache_only)
        outcomes = {
            target.identifier: self._outcome(target, batch, full, cache_only)
            for target in targets
        }
        return outcomes, self._execution(batch)

    async def _shared_batch(
        self,
        key: tuple,
        groups: list[_TargetGroup],
        full: bool,
        cache_only: bool,
    ) -> _Batch:
        async with self._inflight_lock:
            entry = self._inflight.get(key)
            if entry is None:
                task = asyncio.create_task(
                    self._load_batch(groups, full=full, cache_only=cache_only)
                )
                task.add_done_callback(self._consume_exception)
                entry = _InFlightBatch(task)
                self._inflight[key] = entry
            entry.waiters += 1
        try:
            return await asyncio.shield(entry.task)
        finally:
            async with self._inflight_lock:
                entry.waiters -= 1
                if entry.waiters == 0 and self._inflight.get(key) is entry:
                    self._inflight.pop(key)
                    if not entry.task.done():
                        # 在途批次只服务当前等待者；无人等待时取消底层 I/O。
                        entry.task.cancel()

    @staticmethod
    def _consume_exception(task: asyncio.Task[_Batch]) -> None:
        if not task.cancelled():
            task.exception()

    async def _load_batch(
        self,
        groups: list[_TargetGroup],
        *,
        full: bool,
        cache_only: bool,
    ) -> _Batch:
        records: dict[PaperIdentifier, CachedPaperEnrichment] = {}
        errors: dict[PaperIdentifier, Exception] = {}
        cache_hits: set[PaperIdentifier] = set()
        attempted: set[PaperIdentifier] = set()
        succeeded: set[PaperIdentifier] = set()
        cache_write_errors: list[Exception] = []
        pending: dict[bool, list[_TargetGroup]] = {False: [], True: []}

        for group in groups:
            should_read_cache = cache_only or group.needs_core or group.needs_details
            if not should_read_cache or self.cache is None:
                continue
            try:
                cached = await self.cache.get([group.identifier])
            except RedisError as error:
                errors[group.identifier] = error
                continue
            if cached is None:
                continue
            cache_hits.add(group.identifier)
            if self._record_conflicts(group, cached):
                cache_hits.discard(group.identifier)
                errors[group.identifier] = ProviderError(
                    "cached S2 enrichment conflicts with the requested paper identity"
                )
                continue
            records[group.identifier] = cached

        if not cache_only:
            for group in groups:
                if group.identifier in errors:
                    continue
                cached = records.get(group.identifier)
                if cached is not None and not cached.record_found:
                    continue
                needs_core = group.needs_core and cached is None
                needs_details = group.needs_details and (
                    cached is None or not cached.details_confirmed
                )
                if needs_core or needs_details:
                    pending[needs_details].append(group)

            for include_details, request_groups in pending.items():
                if not request_groups:
                    continue
                request_ids = [group.identifier for group in request_groups]
                if self.enricher is None:
                    for identifier in request_ids:
                        errors[identifier] = ServiceException(
                            ScholarErrorCode.CAPABILITY_UNAVAILABLE
                        )
                    continue
                attempted.update(request_ids)
                try:
                    responses = await self.enricher.enrich(
                        request_ids, include_details=include_details
                    )
                except ProviderError as error:
                    errors.update(dict.fromkeys(request_ids, error))
                    continue

                for group, response in zip(request_groups, responses, strict=True):
                    succeeded.add(group.identifier)
                    previous = records.get(group.identifier)
                    if response is None:
                        if previous is not None and previous.record_found:
                            record = CachedPaperEnrichment(
                                enrichment=previous.enrichment,
                                cache_aliases=previous.cache_aliases,
                                record_found=True,
                                details_confirmed=include_details
                                or previous.details_confirmed,
                            )
                            aliases = list(record.cache_aliases)
                        else:
                            record = CachedPaperEnrichment(
                                enrichment=None,
                                cache_aliases=(group.identifier,),
                                record_found=False,
                                details_confirmed=True,
                            )
                            aliases = [group.identifier]
                    else:
                        if self._response_conflicts(group, response, previous):
                            errors[group.identifier] = ProviderError(
                                "S2 response conflicts with the requested paper identity"
                            )
                            succeeded.discard(group.identifier)
                            continue
                        enrichment = self._merge_enrichments(
                            None if previous is None else previous.enrichment,
                            response,
                        )
                        aliases = list(
                            dict.fromkeys(
                                [
                                    group.identifier,
                                    *(
                                        ()
                                        if previous is None
                                        else previous.cache_aliases
                                    ),
                                    *enrichment.identifiers,
                                ]
                            )
                        )
                        record = CachedPaperEnrichment(
                            enrichment=enrichment,
                            cache_aliases=tuple(aliases),
                            record_found=True,
                            details_confirmed=include_details
                            or (previous is not None and previous.details_confirmed),
                        )

                    records[group.identifier] = record
                    if self.cache is not None:
                        try:
                            await self.cache.put(
                                aliases,
                                record,
                                ttl_seconds=self._ttl(record),
                            )
                        except RedisError as error:
                            cache_write_errors.append(error)

        return _Batch(
            records=records,
            errors=errors,
            cache_hits=cache_hits,
            provider_attempted=attempted,
            provider_succeeded=succeeded,
            cache_write_errors=cache_write_errors,
        )

    @classmethod
    def _group(cls, targets: list[EnrichmentTarget], full: bool) -> list[_TargetGroup]:
        grouped: dict[PaperIdentifier, list[EnrichmentTarget]] = {}
        for target in targets:
            grouped.setdefault(target.identifier, []).append(target)
        return [
            _TargetGroup(
                identifier=identifier,
                targets=same_identity,
                needs_core=any(
                    target.paper is None
                    or not cls._core_complete(target.paper.metadata.get("s2", {}))
                    for target in same_identity
                ),
                needs_details=full
                and any(target.paper is None for target in same_identity),
            )
            for identifier, same_identity in grouped.items()
        ]

    @classmethod
    def _batch_key(
        cls, groups: list[_TargetGroup], full: bool, cache_only: bool
    ) -> tuple:
        return (
            full,
            cache_only,
            tuple(
                sorted(
                    (
                        group.identifier.kind.value,
                        group.identifier.value,
                        group.needs_core,
                        group.needs_details,
                        # 与响应冲突校验使用同一身份别名集合，隔离不同离线身份快照。
                        tuple(
                            sorted(
                                {
                                    (alias.kind.value, alias.value)
                                    for target in group.targets
                                    if target.paper is not None
                                    for alias in cls._paper_aliases(target.paper)
                                }
                            )
                        ),
                    )
                    for group in groups
                )
            ),
        )

    def _outcome(
        self,
        target: EnrichmentTarget,
        batch: _Batch,
        full: bool,
        cache_only: bool,
    ) -> _Outcome:
        paper = None if target.paper is None else deepcopy(target.paper)
        offline = {} if paper is None else deepcopy(paper.metadata.get("s2", {}))
        error = batch.errors.get(target.identifier)
        record = batch.records.get(target.identifier)
        if error is not None:
            online = (
                {}
                if record is None or record.enrichment is None
                else record.enrichment.metadata.get("s2", {})
            )
            merged = self._merge_metadata(offline, online)
            if (
                paper is None
                and (full or cache_only)
                and record is not None
                and record.enrichment is not None
            ):
                cached_paper = record.enrichment.paper
                paper = None if cached_paper is None else deepcopy(cached_paper)
            if paper is not None and merged:
                paper.metadata["s2"] = merged
            return _Outcome(
                EnrichmentStatus.FAILED,
                paper,
                {"s2": merged} if merged else None,
                error,
                target.identifier in batch.cache_hits,
            )

        if record is None or not record.record_found:
            status = (
                EnrichmentStatus.NOT_FOUND
                if record is not None or target.paper is None
                else EnrichmentStatus.COMPLETE
                if self._core_complete(offline)
                else EnrichmentStatus.NOT_FOUND
                if cache_only
                else EnrichmentStatus.FAILED
            )
            return _Outcome(
                status,
                paper,
                {"s2": offline} if offline else None,
                None,
                target.identifier in batch.cache_hits,
            )

        enrichment = record.enrichment
        online = {} if enrichment is None else enrichment.metadata.get("s2", {})
        merged = self._merge_metadata(offline, online)
        if paper is None and full and not cache_only:
            if enrichment is not None and enrichment.paper is not None:
                paper = deepcopy(enrichment.paper)
                merged = self._merge_metadata(paper.metadata.get("s2", {}), online)
        elif paper is None and cache_only:
            paper = (
                None
                if enrichment is None or enrichment.paper is None
                else deepcopy(enrichment.paper)
            )
            if paper is not None:
                merged = self._merge_metadata(paper.metadata.get("s2", {}), online)

        if full and target.paper is None and paper is None:
            return _Outcome(
                EnrichmentStatus.FAILED,
                None,
                {"s2": merged} if merged else None,
                ProviderError("S2 full lookup returned no paper details"),
                target.identifier in batch.cache_hits,
            )

        if paper is not None and merged:
            paper.metadata["s2"] = merged
        status = (
            EnrichmentStatus.COMPLETE
            if not cache_only or paper is not None or target.paper is not None
            else EnrichmentStatus.NOT_FOUND
        )
        return _Outcome(
            status,
            paper,
            {"s2": merged} if merged else None,
            cache_hit=target.identifier in batch.cache_hits,
        )

    @staticmethod
    def _paper_aliases(paper: Paper) -> list[PaperIdentifier]:
        aliases = []
        if paper.arxiv_id:
            aliases.append(parse_identifier(paper.arxiv_id))
        if paper.doi:
            aliases.append(parse_identifier(paper.doi))
        s2 = paper.metadata.get("s2", {})
        if s2.get("paper_id"):
            aliases.append(parse_identifier(f"s2:{s2['paper_id']}"))
        if s2.get("corpus_id") is not None:
            aliases.append(parse_identifier(f"corpusid:{s2['corpus_id']}"))
        return aliases

    @classmethod
    def _record_conflicts(
        cls, group: _TargetGroup, record: CachedPaperEnrichment
    ) -> bool:
        if group.identifier not in record.cache_aliases:
            return True
        if not record.record_found or record.enrichment is None:
            return False
        return any(
            cls._target_conflicts(
                target, record.enrichment.identifiers, record.enrichment
            )
            for target in group.targets
        )

    @classmethod
    def _response_conflicts(
        cls,
        group: _TargetGroup,
        response: PaperEnrichment,
        previous: CachedPaperEnrichment | None,
    ) -> bool:
        if any(
            identifier.kind == group.identifier.kind
            and identifier.value != group.identifier.value
            for identifier in response.identifiers
        ):
            return True
        if previous is not None and previous.enrichment is not None:
            for old in previous.enrichment.identifiers:
                if any(
                    old.kind == current.kind and old.value != current.value
                    for current in response.identifiers
                ):
                    return True
        return any(
            cls._target_conflicts(target, response.identifiers, response)
            for target in group.targets
        )

    @classmethod
    def _target_conflicts(
        cls,
        target: EnrichmentTarget,
        response_ids: list[PaperIdentifier],
        enrichment: PaperEnrichment,
    ) -> bool:
        if target.paper is None:
            return False
        known = cls._paper_aliases(target.paper)
        if any(
            actual.kind == identity.kind and actual.value != identity.value
            for identity in known
            for actual in response_ids
        ):
            return True
        offline_corpus = target.paper.metadata.get("s2", {}).get("corpus_id")
        online_corpus = enrichment.metadata.get("s2", {}).get("corpus_id")
        return (
            offline_corpus is not None
            and online_corpus is not None
            and str(offline_corpus) != str(online_corpus)
        )

    @classmethod
    def _merge_enrichments(
        cls,
        previous: PaperEnrichment | None,
        current: PaperEnrichment,
    ) -> PaperEnrichment:
        if previous is None:
            return current
        metadata = deepcopy(previous.metadata)
        for namespace, values in current.metadata.items():
            metadata[namespace] = cls._merge_metadata(
                metadata.get(namespace, {}), values
            )
        return PaperEnrichment(
            identifiers=list(
                dict.fromkeys([*previous.identifiers, *current.identifiers])
            ),
            metadata=metadata,
            paper=current.paper or previous.paper,
        )

    @staticmethod
    def _core_complete(s2: dict[str, Any]) -> bool:
        tldr = s2.get("tldr")
        return (
            s2.get("citation_count") is not None
            and s2.get("influential_citation_count") is not None
            and isinstance(tldr, dict)
            and tldr.get("text") is not None
        )

    @classmethod
    def _merge_metadata(cls, offline: dict, online: dict) -> dict:
        merged = deepcopy(offline)
        for key, value in online.items():
            existing = merged.get(key)
            if key == "tldr" and isinstance(existing, dict) and isinstance(value, dict):
                merged[key] = {
                    name: existing.get(name)
                    if existing.get(name) is not None
                    else value.get(name)
                    for name in dict.fromkeys([*existing, *value])
                }
            elif key not in merged or existing is None:
                merged[key] = deepcopy(value)
        return merged

    def _ttl(self, record: CachedPaperEnrichment) -> int:
        if not record.record_found or record.enrichment is None:
            return self.negative_ttl_seconds
        if record.details_confirmed and record.enrichment.paper is None:
            return self.negative_ttl_seconds
        s2 = record.enrichment.metadata.get("s2", {})
        tldr = s2.get("tldr")
        text = None if tldr is None else tldr.get("text")
        if (
            s2.get("citation_count") is None
            or s2.get("influential_citation_count") is None
            or text is None
            or text == ""
        ):
            return self.negative_ttl_seconds
        return self.positive_ttl_seconds

    @staticmethod
    def _execution(batch: _Batch) -> SourceExecution:
        errors = [*batch.errors.values(), *batch.cache_write_errors]
        completed = batch.provider_succeeded | batch.cache_hits
        if errors:
            status = (
                SourceExecutionStatus.PARTIAL
                if completed
                else SourceExecutionStatus.FAILED
            )
        elif batch.provider_attempted or batch.cache_hits:
            status = SourceExecutionStatus.COMPLETED
        else:
            status = SourceExecutionStatus.NOT_EXECUTED
        return SourceExecution(
            source="s2",
            cache_hit=bool(batch.cache_hits),
            coverage="按规范论文身份补充或读取 S2 事实。",
            status=status,
            error=None if not errors else str(errors[0]),
        )
