from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from common.core.exceptions import ServiceException

from sandbox.application.workspace_manager import WorkspaceManager
from sandbox.core.config.app_settings import settings
from sandbox.core.providers import SandboxProviderManager
from sandbox.domain.entities import CommandTaskDocument, CommandTaskStatus, SandboxState, SandboxDocument
from sandbox.domain.error_codes import SandboxErrorCode
from sandbox.domain.interfaces import ProviderCommandHandle
from sandbox.domain.repositories import CommandTaskRepository, SandboxRepository, WorkspaceRepository


_TERMINAL_STATUSES = {
    CommandTaskStatus.COMPLETED,
    CommandTaskStatus.FAILED,
    CommandTaskStatus.TIMED_OUT,
    CommandTaskStatus.KILLED,
    CommandTaskStatus.LOST,
}
_HARD_TIMEOUT_GRACE_SECONDS = 2


def _utc(value: datetime) -> datetime:
    # Mongo 默认可能返回不带时区的 UTC 时间，统一后再计算剩余超时。
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


class CommandTaskService:
    """统一管理命令审计、后台跟踪及回收保护；当前按单服务实例运行。"""

    def __init__(
        self,
        task_repository: CommandTaskRepository,
        sandbox_repository: SandboxRepository,
        workspace_repository: WorkspaceRepository,
        workspace_manager: WorkspaceManager,
        provider_manager: SandboxProviderManager,
    ) -> None:
        self._tasks = task_repository
        self._sandboxes = sandbox_repository
        self._workspaces = workspace_repository
        self._workspace_manager = workspace_manager
        self._providers = provider_manager
        self._workers: dict[str, asyncio.Task[None]] = {}
        self._events: dict[str, asyncio.Event] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    async def start_command(
        self, *, user_id: str, session_id: str, command: str, timeout_seconds: int,
    ) -> CommandTaskDocument:
        # 在准备 workspace 前创建审计记录，失败有记录可查
        task = CommandTaskDocument(
            task_id=uuid4().hex, user_id=user_id, session_id=session_id,
            command=command, timeout_seconds=timeout_seconds,
        )
        await self._tasks.save(task)

        task_submit = False
        protection_registered = False
        try:
            # 分配或恢复 workspace
            workspace = await self._workspace_manager.allocate(user_id, session_id)

            sandbox = await self._sandboxes.get_by_id(workspace.sandbox_id)
            if (sandbox is None or sandbox.state != SandboxState.USER_ACTIVE or sandbox.bind_user_id != user_id):
                raise ServiceException(SandboxErrorCode.SANDBOX_UNAVAILABLE)

            # 更新 task 状态
            task.sandbox_id = sandbox.sandbox_id
            task.workspace_id = workspace.id
            task.provider_id = sandbox.provider_id
            task.working_directory = workspace.workspace_path
            await self._tasks.update(task)

            # 记录沙箱活跃状态
            active = await self._sandboxes.register_active_command(sandbox.sandbox_id, user_id, datetime.now(timezone.utc))
            if active is None: raise ServiceException(SandboxErrorCode.SANDBOX_UNAVAILABLE)
            protection_registered = True

            # start_command
            handle = await self._providers.start_command(
                sandbox.provider_id, sandbox.base_url,
                command, workspace.workspace_path,
                timeout_seconds,
                sandbox.metadata or {},
            )
            task_submit = True
            task.provider_session_id = handle.provider_session_id
            task.provider_command_id = handle.provider_command_id
            task.status = CommandTaskStatus.RUNNING
            task.started_at = datetime.now(timezone.utc)
            # 更新 task 状态
            await self._tasks.update(task)

            # 非阻塞返回后仍由后台跟踪状态；客户端是否继续查询不影响命令生命周期
            self._events[task.task_id] = asyncio.Event()
            self._locks[task.task_id] = asyncio.Lock()
            self._workers[task.task_id] = asyncio.create_task(self._run_worker(task.task_id, sandbox))
            return task
        except (Exception, asyncio.CancelledError) as exc:
            # 启动响应不确定时记为 LOST；尚未提交 Provider 时失败记为 FAILED
            status = CommandTaskStatus.LOST if task_submit else CommandTaskStatus.FAILED
            await self._finish(task.task_id, status, release_protection=protection_registered, error_message=str(exc))
            raise

    async def execute_command(
        self, *, user_id: str, session_id: str, command: str, timeout_seconds: int,
    ) -> CommandTaskDocument:
        # 阻塞接口组合启动与等待
        task = await self.start_command(
            user_id=user_id, session_id=session_id, command=command, timeout_seconds=timeout_seconds,
        )
        # 等任务进入终态即可返回
        return await self.get_command_result(
            task.task_id, wait=True, wait_timeout_seconds=timeout_seconds + _HARD_TIMEOUT_GRACE_SECONDS + 5,
        )

    async def get_command_result(
        self, task_id: str, *, wait: bool = False, wait_timeout_seconds: float = 0,
    ) -> CommandTaskDocument:
        task = await self._get_task(task_id)
        if wait and task.status not in _TERMINAL_STATUSES:
            # 如需等待，则等待终态或查询超时
            event = self._events.get(task_id)
            if event is not None:
                try:
                    await asyncio.wait_for(event.wait(), timeout=max(0, wait_timeout_seconds))
                except asyncio.TimeoutError:
                    pass
            return await self._get_task(task_id)
        else:
            return task

    async def kill_command(self, task_id: str) -> CommandTaskDocument:
        task = await self._get_task(task_id)
        if task.status in _TERMINAL_STATUSES: return task # 任务是终态直接返回

        try:
            sandbox = await self._sandboxes.get_by_id(task.sandbox_id or "")
            if sandbox is None or not sandbox.base_url:
                raise ServiceException(SandboxErrorCode.SANDBOX_UNAVAILABLE)

            # kill_command
            await self._providers.kill_command(
                task.provider_id, sandbox.base_url,
                ProviderCommandHandle(
                    provider_session_id=task.provider_session_id,
                    provider_command_id=task.provider_command_id,
                ),
                sandbox.metadata or {},
            )
        except Exception as exc:
            # 请求结果不确定时记为 LOST
            await self._finish(task_id, CommandTaskStatus.LOST, release_protection=True, error_message=str(exc))

        # KILLED 表示 AIO 接受了终止请求，不等待远端最终状态确认
        return await self._finish(task_id, CommandTaskStatus.KILLED, release_protection=True)

    async def reconcile_interrupted_tasks(self) -> None:
        """启动时将旧进程留下的任务和回收保护一并清理"""
        unfinished = await self._tasks.list_unfinished()
        # 未完成任务统一LOST
        for task in unfinished:
            task.status = CommandTaskStatus.LOST
            task.finished_at = datetime.now(timezone.utc)
            task.error = "sandbox service restarted before command completed"
            await self._tasks.update(task)

        sandboxes = await self._sandboxes.get_by_states([SandboxState.USER_ACTIVE])
        for sandbox in sandboxes:
            # 直接解除旧进程留下的回收保护
            if sandbox.active_command_count > 0:
                await self._sandboxes.unregister_active_command(
                    sandbox.sandbox_id, count=sandbox.active_command_count,
                )

    async def close(self) -> None:
        # 关停时停止后台读写，保留未完成记录及保护；下次启动再确认远端状态。
        workers = list(self._workers.values())
        for worker in workers:
            worker.cancel()
        await asyncio.gather(*workers, return_exceptions=True)
        self._workers.clear()

    async def _run_worker(self, task_id: str, sandbox: SandboxDocument) -> None:
        stdout_offset = 0
        stderr_offset = 0
        try:
            while True:
                task = await self._get_task(task_id)
                if task.status in _TERMINAL_STATUSES: return # 确认任务尚未是终态

                # 读取 AIO 返回的状态
                result = await self._providers.get_command_result(
                    task.provider_id, sandbox.base_url,
                    ProviderCommandHandle(
                        provider_session_id=task.provider_session_id,
                        provider_command_id=task.provider_command_id,
                    ),
                    stdout_offset, stderr_offset, 1.0,
                    sandbox.metadata or {},
                )
                stdout_offset = result.stdout_offset
                stderr_offset = result.stderr_offset

                # 记录返回值
                async with self._locks[task_id]:
                    task = await self._get_task(task_id)
                    if task.status in _TERMINAL_STATUSES or not (result.stdout or result.stderr):
                        if task.status in _TERMINAL_STATUSES:
                            return
                    else:
                        remaining = max(
                            0,
                            settings.SANDBOX_COMMAND_OUTPUT_MAX_BYTES
                            - len(task.stdout.encode("utf-8"))
                            - len(task.stderr.encode("utf-8")),
                        )
                        for field, value in (("stdout", result.stdout), ("stderr", result.stderr)):
                            encoded = value.encode("utf-8")
                            kept = encoded[:remaining].decode("utf-8", errors="ignore")
                            setattr(task, field, getattr(task, field) + kept)
                            remaining -= len(kept.encode("utf-8"))
                            if len(kept) < len(value):
                                task.output_truncated = True
                        await self._tasks.update(task)

                if result.status in _TERMINAL_STATUSES:
                    await self._finish(
                        task_id, result.status, release_protection=True, exit_code=result.exit_code
                    )
                    return

                # AIO 应在 hard_timeout 后自行终止；宽限后仍无终态就标记 LOST 并允许回收
                if task.started_at is not None:
                    deadline = _utc(task.started_at) + timedelta(seconds=task.timeout_seconds)
                    if datetime.now(timezone.utc) >= deadline + timedelta(seconds=_HARD_TIMEOUT_GRACE_SECONDS):
                        await self._finish(
                            task_id, CommandTaskStatus.LOST, release_protection=True,
                            error_message="AIO did not report a terminal state after hard_timeout",
                        )
                        return
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # Provider 结果无法继续跟踪时记为 LOST
            await self._finish(task_id, CommandTaskStatus.LOST, release_protection=True, error_message=str(exc))
        finally:
            self._workers.pop(task_id, None)
            self._events.pop(task_id, None)
            self._locks.pop(task_id, None)

    async def _finish(
        self,
        task_id: str,
        status: CommandTaskStatus,
        *,
        release_protection: bool,
        exit_code: int | None = None,
        error_message: str | None = None,
    ) -> CommandTaskDocument:
        # 获取任务锁；启动失败可能发生在任务锁创建前，因此不能直接索引 _locks。
        lock = self._locks.setdefault(task_id, asyncio.Lock())
        async with lock:  # 获取任务锁
            task = await self._get_task(task_id)
            if task.status in _TERMINAL_STATUSES: return task # 确认任务尚未是终态

            task.status = status
            task.finished_at = datetime.now(timezone.utc)
            task.exit_code = exit_code
            task.error = error_message
            # 更新 task 状态
            await self._tasks.update(task)
            # 只有成功登记过回收保护的任务才解除一次；task.sandbox_id 本身不能作为判断依据。
            if release_protection and task.sandbox_id:
                await self._sandboxes.unregister_active_command(task.sandbox_id)

            event = self._events.get(task_id)
            if event is not None:
                event.set()
            return task

    async def _get_task(self, task_id: str) -> CommandTaskDocument:
        task = await self._tasks.get_by_id(task_id)
        if task is None: raise ServiceException(SandboxErrorCode.TASK_UNAVAILABLE)
        return task
