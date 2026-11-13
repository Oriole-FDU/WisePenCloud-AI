from __future__ import annotations

import asyncio
from datetime import timedelta, datetime, timezone
from pathlib import Path

from common.logger import error, warn
from sandbox.application.container_manager import ContainerManager, ContainerStatus
from sandbox.application.host_health_checker import HostHealthChecker, PressureState
from sandbox.application.workspace_manager import WorkspaceManager
from sandbox.core.config.app_settings import settings
from sandbox.core.providers import SandboxProviderManager
from sandbox.domain.entities import SandboxDocument, SandboxState
from sandbox.domain.repositories import SandboxRepository


# 内置脚本随 sandbox 包一起分发
# 目标目录为容器内的专用脚本目录
_BUILTIN_SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "builtin_scripts"
_CONTAINER_SCRIPTS_DIR = "/builtin_scripts"


class Watcher:
    """后台容器池维护器
    """

    def __init__(
        self,
        sandbox_repository: SandboxRepository,
        sandbox_provider_manager: SandboxProviderManager,
        container_manager: ContainerManager,
        workspace_manager: WorkspaceManager,
        host_health_checker: HostHealthChecker | None = None,
    ) -> None:
        self._sandbox_repository = sandbox_repository
        self._sandbox_provider_manager = sandbox_provider_manager
        self._container_manager = container_manager
        self._workspace_manager = workspace_manager
        self._host_health_checker = host_health_checker or HostHealthChecker(container_manager)
        self._stop = asyncio.Event()
        self._lock = asyncio.Lock()

    async def maintain_sandbox_pool(self) -> int:
        """检查沙箱池并按目标数量补充预热容器"""

        # 同一时刻只允许进行一次容器状态检查
        async with self._lock:
            host_health = await self._host_health_checker.checker()

            if host_health.state == PressureState.HIGH:
                warn(
                    "sandbox host pressure detected; warmup paused",
                    disk_usage=host_health.disk_usage,
                    cpu_usage_percent=host_health.cpu_usage_percent,
                    disk_pressure=host_health.disk_state == PressureState.HIGH,
                    cpu_pressure=host_health.cpu_state == PressureState.HIGH,
                )
                # CPU 压力仅暂停扩展池；磁盘压力会回收 READY/WARMING 的沙箱
                if host_health.disk_state == PressureState.HIGH:
                    await self.relieve_host_pressure()
                return 0

            # 获取 MongoDB 中 就绪和正在预热的容器
            sandboxes = await self._sandbox_repository.get_by_states(
                [SandboxState.READY, SandboxState.WARMING, SandboxState.DESTROYING],
            )

            # 轮换过期的预热容器
            now = datetime.now(timezone.utc)
            ready_cutoff = now - timedelta(seconds=settings.pool.ready_max_age_seconds)
            expired_sandbox = sorted(
                (
                    sandbox for sandbox in sandboxes
                    if sandbox.state == SandboxState.READY and sandbox.updated_at <= ready_cutoff
                ),
                key=lambda sandbox: sandbox.updated_at,
            )

            if expired_sandbox:
                candidate = expired_sandbox[0] # 同批预热的容器通常同时到期，每轮只替换最老的一个，避免就绪池瞬间归零
                await self._sandbox_repository.change_state(
                    candidate.sandbox_id, SandboxState.DESTROYING
                )
                await self.force_destroy_specified_sandbox(candidate)
                # sandboxes 保存的是 SandboxDocument，删除已进入销毁流程的候选对象，避免后续重复统计
                sandboxes.remove(candidate)

            ready_count = 0
            warming_count = 0
            force_destroy_sandbox_list: list[SandboxDocument] = []
            for sandbox in sandboxes:

                if sandbox.state == SandboxState.READY:
                    sandbox_ready = await self._sandbox_provider_manager.check_ready(
                        sandbox.provider_id,
                        sandbox.base_url,
                        sandbox.metadata or {},
                    )
                    if sandbox_ready:
                        ready_count += 1
                    else:
                        force_destroy_sandbox_list.append(sandbox) # 强制销毁不健康的容器（由于未分配，可直接强制销毁）

                elif sandbox.state == SandboxState.WARMING:
                    # 检查正在预热的容器的实际状态
                    sandbox_ready = await self._sandbox_provider_manager.check_ready(
                        sandbox.provider_id,
                        sandbox.base_url,
                        sandbox.metadata or {},
                    )
                    if sandbox_ready: # 如果已经预热好，就新增就绪数，并更新状态
                        ready_count += 1
                        await self._sandbox_repository.change_state(sandbox.sandbox_id, SandboxState.READY)
                    elif now > sandbox.updated_at + timedelta(seconds=settings.pool.warmup.timeout_seconds):
                        force_destroy_sandbox_list.append(sandbox)  # 强制销毁未能正常预热的容器（由于未分配，可直接强制销毁）
                    else:
                        warming_count += 1
                        # 没有就绪也没有超时的容器下次再检查

                elif sandbox.state == SandboxState.DESTROYING:
                    # 检查正在销毁的容器的实际状态
                    container_status = await self._container_manager.check_container_status(sandbox.container_id)
                    if container_status == ContainerStatus.NOT_FOUND:
                        await self._sandbox_repository.change_state(
                            sandbox.sandbox_id,
                            SandboxState.DESTROYED,
                            clear_user_binding=True,
                        )
                    elif now > sandbox.updated_at + timedelta(seconds=settings.pool.destroy.timeout_seconds):
                        force_destroy_sandbox_list.append(sandbox)  # 强制销毁正在销毁的容器（不等待容器自然销毁）
                    # 没有超时的容器下次再检查

            if force_destroy_sandbox_list:
                await self.force_destroy_specified_sandboxes(force_destroy_sandbox_list)

            existing = ready_count + warming_count
            if existing >= settings.pool.target_ready:
                return 0 # 当前就绪和正在预热的容器数量超过了需要预热的总数量，不需要处理

            # 尝试预热容器到指定数量，连续三次失败即终止
            return await self.warm_sandboxes(settings.pool.target_ready - existing)

    async def warm_sandboxes(self, plan_quantity: int) -> int:
        # 尝试预热容器到指定数量，连续三次失败即终止
        created = 0
        failures = 0

        provider_id = settings.provider.active_id
        while created < plan_quantity and failures < settings.pool.warmup.max_retries:
            sandbox_provision = self._sandbox_provider_manager.create_provision(provider_id)
            container_id: str | None = None
            try:
                container_id = await self._container_manager.create(
                    sandbox_provision.image,
                    environment=sandbox_provision.container_environment,
                )
                base_url = await self._container_manager.get_container_base_url(container_id)
                # 预热时把内置脚本加载进容器专用目录，供 AI 在会话中访问
                await self._container_manager.copy_scripts_to_container(
                    container_id, str(_BUILTIN_SCRIPTS_DIR), _CONTAINER_SCRIPTS_DIR,
                )
                sandbox = SandboxDocument(
                    container_id=container_id,
                    metadata=sandbox_provision.provider_metadata,
                    provider_id=provider_id.value,
                    base_url=base_url,
                    state=SandboxState.WARMING,
                    updated_at=datetime.now(timezone.utc)
                )
                await self._sandbox_repository.save(sandbox)
                created += 1
                failures = 0
            except Exception as exc:
                if container_id is not None:
                    try:
                        await self._container_manager.destroy(container_id)
                    except Exception as cleanup_exc:
                        error("sandbox warm cleanup failed", exc=cleanup_exc, container_id=container_id)
                failures += 1
                error("sandbox warm failed", exc=exc)

        return created

    async def force_destroy_specified_sandboxes(self, sandboxes: list[SandboxDocument]) -> None:
        for sandbox in sandboxes:
                await self.force_destroy_specified_sandbox(sandbox)

    async def force_destroy_specified_sandbox(self, sandbox: SandboxDocument) -> bool:
        try:
            await self._container_manager.destroy(sandbox.container_id)
            await self._sandbox_repository.change_state(sandbox.sandbox_id, SandboxState.DESTROYED, clear_user_binding=True)
            return True
        except Exception as exc:
            error("sandbox force destroy failed", exc=exc, sandbox_id=sandbox.sandbox_id)
            return False

    async def relieve_host_pressure(self) -> int:
        """按最老 READY、最老 WARMING 的顺序回收未分配容器，直至磁盘恢复。"""
        candidates = await self._sandbox_repository.get_by_states(
            [SandboxState.READY, SandboxState.WARMING],
        )
        candidates.sort(
            key=lambda sandbox: (sandbox.state != SandboxState.READY, sandbox.updated_at),
        )

        destroyed = 0
        for candidate in candidates:
            host_health = await self._host_health_checker.checker()
            if host_health.disk_state == PressureState.NORMAL:
                return destroyed

            claimed = await self._sandbox_repository.change_state(
                candidate.sandbox_id,
                SandboxState.DESTROYING
            )
            if claimed is not None and await self.force_destroy_specified_sandbox(claimed):
                destroyed += 1

        # 无未分配的容器可以销毁
        host_health = await self._host_health_checker.checker()
        if host_health.disk_state == PressureState.HIGH:
            # 仍为高位
            error(
                "sandbox host pressure remains after reclaiming all unassigned containers",
                disk_usage=host_health.disk_usage,
            )
        return destroyed

    async def run(self) -> None:
        # 循环维护沙箱池
        while not self._stop.is_set():
            try:
                await self.maintain_sandbox_pool()
            except Exception as exc:
                error("sandbox watcher iteration failed", exc=exc)
            try:
                await self.reclaim_idle_sandboxes()
            except Exception as exc:
                error("sandbox reclaim iteration failed", exc=exc)
            try:
                await asyncio.wait_for(
                    self._stop.wait(),
                    timeout=settings.pool.watcher_interval_seconds,
                )
            except asyncio.TimeoutError:
                continue

    def stop(self) -> None:
        # 请求 watcher 循环停止
        self._stop.set()

    async def reclaim_idle_sandboxes(self) -> None:
        # 原子认领满足空闲条件的 USER_ACTIVE 沙箱
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=settings.pool.idle_timeout_seconds)

        active = await self._sandbox_repository.get_by_states([SandboxState.USER_ACTIVE])
        retiring: list[SandboxDocument] = []
        for candidate in active:
            claimed = await self._sandbox_repository.claim_idle_sandbox(
                candidate.sandbox_id, cutoff,
            )
            if claimed is not None:
                retiring.append(claimed)

        # 处理进入 RETIRING 状态的沙箱
        retiring.extend(await self._sandbox_repository.get_by_states([SandboxState.RETIRING]))
        for sandbox in retiring:
            try:
                await self._workspace_manager.reclaim(sandbox)
            except Exception as exc:
                error("sandbox reclaim failed", exc=exc, sandbox_id=sandbox.sandbox_id)
