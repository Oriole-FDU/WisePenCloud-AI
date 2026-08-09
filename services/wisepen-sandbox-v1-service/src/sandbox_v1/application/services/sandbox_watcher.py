from __future__ import annotations

import asyncio
from datetime import timedelta, datetime, timezone

from common.logger import error
from sandbox_v1.core.config.app_settings import settings
from sandbox_v1.domain.entities import SandboxDocument, SandboxState
from sandbox_v1.domain.interfaces import SandboxRegistry, ContainerManager, ContainerStatus
from sandbox_v1.domain.interfaces.sandbox_registry import SandboxProviderInfo
from sandbox_v1.domain.repositories import SandboxRepository


class Watcher:
    """后台容器池维护器
    """

    def __init__(
        self,
        sandbox_repository: SandboxRepository,
        sandbox_registry: SandboxRegistry,
        container_manager: ContainerManager
    ) -> None:
        self._sandbox_repository = sandbox_repository
        self._sandbox_registry = sandbox_registry
        self._container_manager = container_manager
        self._stop = asyncio.Event()
        self._lock = asyncio.Lock()

    async def check_container_status(self) -> int:
        """检查容器状态"""

        # 同一时刻只允许进行一次容器状态检查
        async with self._lock:
            # 获取 MongoDB 中 就绪和正在预热的容器
            sandboxes = await self._sandbox_repository.get_by_states([SandboxState.READY,SandboxState.WARMING, SandboxState.DESTROYING])

            ready_count = 0
            warming_count = 0
            force_destroy_sandbox_list: list[SandboxDocument] = []
            for sandbox in sandboxes:

                if sandbox.state == SandboxState.READY:
                    # 检查健康状态
                    sandbox_ready = await self._sandbox_registry.check_ready(sandbox.provider_id, sandbox.endpoint)
                    if sandbox_ready: ready_count += 1
                    else: force_destroy_sandbox_list.append(sandbox) # 强制销毁不健康的容器（由于未分配，可直接强制销毁）

                elif sandbox.state == SandboxState.WARMING:
                    # 检查正在预热的容器的实际状态
                    sandbox_ready = await self._sandbox_registry.check_ready(sandbox.provider_id, sandbox.endpoint)
                    if sandbox_ready: # 如果已经预热好，就新增就绪数，并更新状态
                        ready_count += 1
                        await self._sandbox_repository.change_state(sandbox.sandbox_id, SandboxState.READY)
                    elif datetime.now(timezone.utc) > sandbox.updated_at + timedelta(seconds=settings.SANDBOX_WARMUP_TIMEOUT_SECONDS):  # 超时
                        force_destroy_sandbox_list.append(sandbox)  # 强制销毁未能正常预热的容器（由于未分配，可直接强制销毁）
                    else:
                        warming_count += 1
                        # 没有就绪也没有超时的容器下次再检查

                elif sandbox.state == SandboxState.DESTROYING:
                    # 检查正在销毁的容器的实际状态
                    container_status = await self._container_manager.check_container_status(sandbox.container_id)
                    if container_status == ContainerStatus.NOT_FOUND:
                        await self._sandbox_repository.change_state(sandbox.sandbox_id, SandboxState.DESTROYED)
                    elif datetime.now(timezone.utc) > sandbox.updated_at + timedelta(seconds=settings.SANDBOX_DESTROY_TIMEOUT_SECONDS):
                            force_destroy_sandbox_list.append(sandbox)  # 强制销毁正在销毁的容器（不等待容器自然销毁）
                    # 没有超时的容器下次再检查

            if force_destroy_sandbox_list:
                await self.force_destroy_specified_sandbox(force_destroy_sandbox_list)

            existing = ready_count + warming_count
            if existing >= settings.SANDBOX_TARGET_READY:
                return 0 # 当前就绪和正在预热的容器数量超过了需要预热的总数量，不需要处理

            # 尝试预热容器到指定数量，连续三次失败即终止
            return await self.warm_sandboxes(settings.SANDBOX_TARGET_READY - existing)

    async def warm_sandboxes(self, plan_quantity: int):
        # 尝试预热容器到指定数量，连续三次失败即终止
        created = 0
        failures = 0

        sandbox_provider_info: SandboxProviderInfo = self._sandbox_registry.get_sandbox_provider_info(settings.SANDBOX_PROVIDER_ID)

        while created < plan_quantity and failures < 3:
            try:
                container_id = await self._container_manager.create(sandbox_provider_info.start_spec.image)
                sandbox = SandboxDocument(
                    container_id=container_id,
                    provider_id=settings.SANDBOX_PROVIDER_ID,
                    endpoint=sandbox_provider_info.endpoint,
                    state=SandboxState.WARMING,
                    updated_at=datetime.now(timezone.utc)
                )
                await self._sandbox_repository.save(sandbox)
                created += 1
                failures = 0
            except Exception as exc:
                failures += 1
                error("sandbox warm failed", exc=exc)

        return created

    async def force_destroy_specified_sandbox(self, sandboxes: list[SandboxDocument]):
        # 强制销毁指定的容器
        for sandbox in sandboxes:
            try:
                await self._container_manager.destroy(sandbox.container_id)
                await self._sandbox_repository.change_state(sandbox.sandbox_id, SandboxState.DESTROYED)
            except Exception as exc:
                error("sandbox force destroy failed", exc=exc, sandbox_id=sandbox.sandbox_id)

    async def run(self) -> None:
        # 循环执行 check_container_status
        while not self._stop.is_set():
            await self.check_container_status()
            try:
                await asyncio.wait_for(
                    self._stop.wait(),
                    timeout=settings.SANDBOX_WATCHER_INTERVAL_SECONDS,
                )
            except asyncio.TimeoutError:
                continue

    def stop(self) -> None:
        """请求 watcher 循环停止。"""

        # 只设置停止信号，不等待 run 协程退出。
        self._stop.set()
