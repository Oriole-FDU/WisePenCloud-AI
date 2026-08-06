from __future__ import annotations

from dataclasses import dataclass, replace

from common.core.exceptions import ServiceException
from common.logger import error, info, warn

from sandbox_v1.domain.entities import DiscoveredSandbox, SandboxRecord, SandboxState
from sandbox_v1.domain.error_codes import SandboxErrorCode
from sandbox_v1.domain.interfaces.sandbox_provider import SandboxProvider
from sandbox_v1.domain.repositories import SandboxRepository


_POOL_AUTHORITY_STATES = (
    # 启动对账只补偿“池态”记录；用户态容器由后续 Workspace/Execution 恢复流程处理。
    SandboxState.CREATING,
    SandboxState.WARMING,
    SandboxState.READY,
    SandboxState.DESTROYING,
)


@dataclass(frozen=True)
class StartupReconcileResult:
    """启动容器对账的结果统计。"""

    discovered: int = 0
    matched_ready: int = 0
    orphan_destroyed: int = 0
    inflight_destroyed: int = 0
    unhealthy_destroyed: int = 0
    missing_marked_lost: int = 0
    destroying_finished: int = 0


class SandboxStartupReconciler:
    """启动时对账 provider 发现容器与 Repository 权威记录。

    Docker/provider labels 只用于发现候选容器；Repository 决定容器是否属于当前池。
    当前阶段只补偿池态记录，USER_ACTIVE/RETIRING 等用户态恢复留给后续
    Workspace/Execution 流程，避免在启动阶段引入进程本地兜底路径。
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
        """执行一次完整启动对账，并返回本轮处理结果。

        流程分两轮：先以 Repository 池态记录为权威，补偿缺失容器或校验已发现
        容器；再以 provider 发现结果为线索，销毁 Repository 完全未知的孤儿容器。
        """

        # 发现当前运行环境里的托管容器；发现失败说明 provider 当前不可用。
        try:
            discovered = await self._provider.list_managed()
        except ServiceException:
            raise
        except Exception as exc:
            error("启动容器发现失败", exc=exc)
            raise ServiceException(SandboxErrorCode.SANDBOX_UNAVAILABLE, "启动容器发现失败") from exc

        # 读取 Repository 认可的池态记录，并建立 sandbox_id 索引方便两轮对账。
        records = {
            record.ref.sandbox_id: record
            for record in await self._repository.records_in(_POOL_AUTHORITY_STATES)
        }
        discovered_by_id = {item.ref.sandbox_id: item for item in discovered}
        result = StartupReconcileResult(discovered=len(discovered))

        # 第一轮：以 Repository 为权威，补偿缺失记录或校验已发现容器。
        for sandbox_id, record in records.items():
            item = discovered_by_id.get(sandbox_id)
            if item is None:
                result = await self._compensate_missing(record, result)
                continue
            result = await self._reconcile_authoritative_record(record, item, result)

        # 第二轮：以 provider 发现结果为线索，清理 Repository 完全未知的孤儿容器。
        for item in discovered:
            if item.ref.sandbox_id not in records:
                # 用户态容器不属于阶段 2 的池补偿范围，但只要 Repository 有记录，
                # 就说明它不是孤儿；后续 Workspace/Execution 阶段再处理用户态恢复。
                if await self._repository.get(item.ref.sandbox_id) is not None:
                    self._metrics.increment("startup_authoritative_non_pool_retained")
                    continue
                result = await self._destroy_orphan(item, result)

        # 输出启动对账汇总日志，供启动观测和测试排查使用。
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
        """对账 Repository 有池态记录、provider 也发现了容器的情况。

        DESTROYING 继续销毁并标记完成；CREATING/WARMING 属于重启前的 in-flight
        中间态，直接销毁以避免旧 ready 回调污染新进程；READY 只有仍在运行且健康
        检查通过才保留，其余 READY 异常情况统一走 unhealthy 销毁路径。
        """

        # 销毁流程中的容器：启动后继续完成销毁。
        if record.state == SandboxState.DESTROYING:
            await self._destroy_and_mark(record, item, "startup_destroying")
            return self._replace(result, destroying_finished=result.destroying_finished + 1)

        # 创建/预热中的容器：重启后中间态不可恢复，直接销毁。
        if record.state in (SandboxState.CREATING, SandboxState.WARMING):
            await self._destroy_and_mark(record, item, "startup_inflight")
            return self._replace(result, inflight_destroyed=result.inflight_destroyed + 1)

        # READY 容器：只有仍在运行且健康检查通过才继续保留。
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

        # 健康检查失败、不健康或未运行：统一走销毁补偿。
        await self._destroy_and_mark(record, item, "startup_unhealthy")
        return self._replace(result, unhealthy_destroyed=result.unhealthy_destroyed + 1)

    async def _compensate_missing(
        self,
        record: SandboxRecord,
        result: StartupReconcileResult,
    ) -> StartupReconcileResult:
        """补偿 Repository 认为池容器存在、但 provider 没发现容器的情况。

        DESTROYING 记录缺失可视为销毁已经完成，直接转 DESTROYED；其他池态记录
        缺失说明权威记录和实际容器不一致，不能当作正常销毁，只能标记 LOST。
        """

        # DESTROYING 记录缺失时，缺失本身就是销毁完成的证据。
        if record.state == SandboxState.DESTROYING:
            await self._repository.transition(
                record.ref.sandbox_id,
                SandboxState.DESTROYING,
                SandboxState.DESTROYED,
                error="container missing during startup reconcile",
            )
            self._metrics.increment("startup_destroying_compensated")
            return self._replace(result, destroying_finished=result.destroying_finished + 1)

        # 非 DESTROYING 池记录缺失时，不能视为正常销毁，只能标记 LOST。
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
        """销毁 Repository 完全未知的 provider 容器。

        orphan 的定义是 provider 发现了容器，但 Repository 没有任何对应记录。
        这种容器不属于当前服务权威状态，启动时应显式销毁。
        """

        # 销毁 Repository 完全未知的 provider 容器。
        try:
            await self._provider.destroy(item.ref, "startup_orphan")
        except Exception as exc:
            # 销毁失败时让启动对账失败，避免静默遗留未知容器。
            error(
                "启动对账销毁孤儿容器失败",
                exc=exc,
                sandbox_id=item.ref.sandbox_id,
                provider_id=item.ref.provider_id,
            )
            raise ServiceException(SandboxErrorCode.SANDBOX_UNAVAILABLE,"启动对账销毁孤儿容器失败") from exc
        self._metrics.increment("startup_orphan_destroyed")
        return self._replace(result, orphan_destroyed=result.orphan_destroyed + 1)

    async def _destroy_and_mark(
        self,
        record: SandboxRecord,
        item: DiscoveredSandbox,
        reason: str,
    ) -> None:
        """销毁有 Repository 权威记录的容器，并同步落最终状态。

        统一顺序是：先把 Repository 状态推进到 DESTROYING，再执行 provider 层
        销毁；销毁成功后转 DESTROYED，销毁失败后转 LOST 并抛出服务异常。
        """

        # 先进入 DESTROYING，保证 Repository 中有可观测的销毁中状态。
        await self._transition_to_destroying(record, reason)
        try:
            # 执行 provider 层实际销毁。
            await self._provider.destroy(item.ref, reason)
        except Exception as exc:
            # 销毁失败：Repository 记录转 LOST，暴露启动失败。
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

        # 销毁成功：Repository 记录转 DESTROYED。
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
        """把记录安全推进到 DESTROYING，兼容已经处于销毁中的记录。"""

        if record.state == SandboxState.DESTROYING:
            # Repository transition 是严格状态机，DESTROYING -> DESTROYING 不合法。
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
