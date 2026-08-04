from __future__ import annotations

from dataclasses import dataclass, replace

from common.core.exceptions import ServiceException
from common.logger import error, info, warn

from sandbox_v1.domain.entities import DiscoveredSandbox, SandboxRecord, SandboxState
from sandbox_v1.domain.error_codes import SandboxErrorCode
from sandbox_v1.domain.interfaces.sandbox_provider import SandboxProvider
from sandbox_v1.domain.repositories import SandboxRepository


_POOL_AUTHORITY_STATES = (
    SandboxState.CREATING,
    SandboxState.WARMING,
    SandboxState.READY,
    SandboxState.DESTROYING,
)


@dataclass(frozen=True)
class StartupReconcileResult:
    discovered: int = 0
    matched_ready: int = 0
    orphan_destroyed: int = 0
    inflight_destroyed: int = 0
    unhealthy_destroyed: int = 0
    missing_marked_lost: int = 0
    destroying_finished: int = 0


class SandboxStartupReconciler:
    """Reconcile Docker-discovered containers with repository authority.

    Docker labels are immutable and only help discover candidate containers.
    The repository decides whether a container belongs to the current pool.
    This keeps phase 2 aligned with the future Mongo authority without adding
    a process-local fallback path.
    """

    def __init__(
        self,
        repository: SandboxRepository,
        provider: SandboxProvider,
    ) -> None:
        self._repository = repository
        self._provider = provider
        self._metrics = repository.metrics

    async def reconcile(self) -> StartupReconcileResult:
        try:
            discovered = await self._provider.list_managed()
        except ServiceException:
            raise
        except Exception as exc:
            error("启动容器发现失败", exc=exc)
            raise ServiceException(
                SandboxErrorCode.SANDBOX_UNAVAILABLE,
                "启动容器发现失败",
            ) from exc

        records = {
            record.ref.sandbox_id: record
            for record in await self._repository.records_in(_POOL_AUTHORITY_STATES)
        }
        discovered_by_id = {item.ref.sandbox_id: item for item in discovered}
        result = StartupReconcileResult(discovered=len(discovered))

        for sandbox_id, record in records.items():
            item = discovered_by_id.get(sandbox_id)
            if item is None:
                result = await self._compensate_missing(record, result)
                continue
            result = await self._reconcile_authoritative_record(record, item, result)

        for item in discovered:
            if item.ref.sandbox_id not in records:
                # 用户态容器不属于阶段 2 的池补偿范围，但只要 Repository 有记录，
                # 就说明它不是孤儿；后续 Workspace/Execution 阶段再处理用户态恢复。
                if await self._repository.get(item.ref.sandbox_id) is not None:
                    self._metrics.increment("startup_authoritative_non_pool_retained")
                    continue
                result = await self._destroy_orphan(item, result)

        info(
            "启动容器对账完成",
            discovered=result.discovered,
            matched_ready=result.matched_ready,
            orphan_destroyed=result.orphan_destroyed,
            inflight_destroyed=result.inflight_destroyed,
            unhealthy_destroyed=result.unhealthy_destroyed,
            missing_marked_lost=result.missing_marked_lost,
            destroying_finished=result.destroying_finished,
        )
        return result

    async def _reconcile_authoritative_record(
        self,
        record: SandboxRecord,
        item: DiscoveredSandbox,
        result: StartupReconcileResult,
    ) -> StartupReconcileResult:
        if record.state == SandboxState.DESTROYING:
            await self._destroy_and_mark(record, item, "startup_destroying")
            return self._replace(result, destroying_finished=result.destroying_finished + 1)

        if record.state in (SandboxState.CREATING, SandboxState.WARMING):
            # 重启后旧 readiness 回调不再可信；销毁 in-flight 容器防止旧 generation 回写。
            await self._destroy_and_mark(record, item, "startup_inflight")
            return self._replace(result, inflight_destroyed=result.inflight_destroyed + 1)

        if record.state == SandboxState.READY and item.running:
            try:
                health = await self._provider.health(item.ref)
            except Exception as exc:
                warn(
                    "启动对账健康检查失败，销毁 READY 容器",
                    exc=exc,
                    sandbox_id=record.ref.sandbox_id,
                    provider_id=record.ref.provider_id,
                )
            else:
                if health.healthy:
                    self._metrics.increment("startup_ready_claims")
                    return self._replace(result, matched_ready=result.matched_ready + 1)
                warn(
                    "启动对账发现 READY 容器不健康，准备销毁",
                    sandbox_id=record.ref.sandbox_id,
                    provider_id=record.ref.provider_id,
                    status=health.status,
                )

        await self._destroy_and_mark(record, item, "startup_unhealthy")
        return self._replace(result, unhealthy_destroyed=result.unhealthy_destroyed + 1)

    async def _compensate_missing(
        self,
        record: SandboxRecord,
        result: StartupReconcileResult,
    ) -> StartupReconcileResult:
        if record.state == SandboxState.DESTROYING:
            await self._repository.transition(
                record.ref.sandbox_id,
                SandboxState.DESTROYING,
                SandboxState.DESTROYED,
                error="container missing during startup reconcile",
            )
            self._metrics.increment("startup_destroying_compensated")
            return self._replace(result, destroying_finished=result.destroying_finished + 1)

        await self._transition_to_destroying(record, "container missing during startup reconcile")
        await self._repository.transition(
            record.ref.sandbox_id,
            SandboxState.DESTROYING,
            SandboxState.LOST,
            error="container missing during startup reconcile",
        )
        self._metrics.increment("startup_missing_lost")
        return self._replace(result, missing_marked_lost=result.missing_marked_lost + 1)

    async def _destroy_orphan(
        self,
        item: DiscoveredSandbox,
        result: StartupReconcileResult,
    ) -> StartupReconcileResult:
        try:
            await self._provider.destroy(item.ref, "startup_orphan")
        except Exception as exc:
            error(
                "启动对账销毁孤儿容器失败",
                exc=exc,
                sandbox_id=item.ref.sandbox_id,
                provider_id=item.ref.provider_id,
            )
            raise ServiceException(
                SandboxErrorCode.SANDBOX_UNAVAILABLE,
                "启动对账销毁孤儿容器失败",
            ) from exc
        self._metrics.increment("startup_orphan_destroyed")
        return self._replace(result, orphan_destroyed=result.orphan_destroyed + 1)

    async def _destroy_and_mark(
        self,
        record: SandboxRecord,
        item: DiscoveredSandbox,
        reason: str,
    ) -> None:
        await self._transition_to_destroying(record, reason)
        try:
            await self._provider.destroy(item.ref, reason)
        except Exception as exc:
            await self._repository.transition(
                record.ref.sandbox_id,
                SandboxState.DESTROYING,
                SandboxState.LOST,
                error=str(exc)[:200],
            )
            self._metrics.increment("startup_destroy_failures")
            error(
                "启动对账销毁权威记录容器失败",
                exc=exc,
                sandbox_id=record.ref.sandbox_id,
                provider_id=item.ref.provider_id,
                reason=reason,
            )
            raise ServiceException(
                SandboxErrorCode.SANDBOX_UNAVAILABLE,
                "启动对账销毁容器失败",
            ) from exc

        await self._repository.transition(
            record.ref.sandbox_id,
            SandboxState.DESTROYING,
            SandboxState.DESTROYED,
            error=reason,
        )
        self._metrics.increment("startup_destroy_successes")

    async def _transition_to_destroying(
        self,
        record: SandboxRecord,
        reason: str,
    ) -> None:
        if record.state == SandboxState.DESTROYING:
            return
        await self._repository.transition(
            record.ref.sandbox_id,
            record.state,
            SandboxState.DESTROYING,
            error=reason,
        )

    @staticmethod
    def _replace(
        result: StartupReconcileResult,
        **changes: int,
    ) -> StartupReconcileResult:
        return replace(result, **changes)
