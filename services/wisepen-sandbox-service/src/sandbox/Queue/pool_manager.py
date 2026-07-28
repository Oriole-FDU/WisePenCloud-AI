"""
ContainerPoolManager — 统一封装容器队列的所有组件。

创建、启动、停止、销毁 ContainerQueue / Scheduler / FileManager / Watcher。
sandbox-service 和 aio-gateway 均可复用一个调用点。
"""
from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, field

from sandbox.Queue.container_queue import ContainerQueue
from sandbox.Queue.file_manager import FileManager
from sandbox.Queue.scheduler import Scheduler
from sandbox.Queue.watcher import Watcher
from sandbox.Queue.store import WorkspaceStore, LocalWorkspaceStore, MongoWorkspaceStore


@dataclass(frozen=True)
class SandboxProfile:
    """沙箱镜像专用属性 — 更换镜像时只需修改此配置。
    所有硬编码值统一收敛于此，ContainerQueue / FileManager / PathTranslator 均从 PoolConfig 获取。
    """
    container_user: str = "gem:gem"
    workspace_root: str = "/home/gem/workspaces"
    virtual_root: str = "/workspace"
    vnc_port: int = 8080
    websockify_port: int = 6080
    role_label: str = "wisepen.role=aio-worker"
    manager_label: str = "wisepen.manager=sandbox-manager"
    shm_size: str = "2gb"
    startup_wait_seconds: int = 5


@dataclass(frozen=True)
class PoolConfig:
    """容器池配置，一次性传入，不可变。"""
    image: str = "ghcr.io/agent-infra/sandbox:latest"
    min_idle: int = 2
    max_total: int = 8
    workspace_cache: str = "/workspaces"
    allocation_timeout: float = 5.0
    session_max: int = 3
    dirty_ttl: float = 60.0
    workspace_cleanup_ttl: float = 7 * 24 * 3600
    workspace_cleanup_interval: float = 3600.0
    network: str = "sandbox-net"
    profile: SandboxProfile = field(default_factory=SandboxProfile)
    # 持久化后端: "local" 或 "mongo"
    store_backend: str = "local"
    mongo_url: str = "mongodb://127.0.0.1:27017"
    mongo_db: str = "wisepen_sandbox"

    @property
    def container_user(self) -> str:
        return self.profile.container_user

    @property
    def workspace_root(self) -> str:
        return self.profile.workspace_root

    @property
    def virtual_root(self) -> str:
        return self.profile.virtual_root

    @property
    def vnc_port(self) -> int:
        return self.profile.vnc_port

    @property
    def websockify_port(self) -> int:
        return self.profile.websockify_port

    @property
    def role_label(self) -> str:
        return self.profile.role_label

    @property
    def manager_label(self) -> str:
        return self.profile.manager_label


class ContainerPoolManager:
    """容器池总管理器。"""

    def __init__(self, config: PoolConfig | None = None):
        cfg = config or PoolConfig()
        self._cfg = cfg
        self._queue = ContainerQueue(
            image=cfg.image,
            min_idle=cfg.min_idle,
            max_total=cfg.max_total,
            workspace_cache=cfg.workspace_cache,
            network=cfg.network,
            profile=cfg.profile,
        )
        self._scheduler = Scheduler(
            self._queue,
            allocation_timeout=cfg.allocation_timeout,
            session_max=cfg.session_max,
        )
        self._file_manager = FileManager(
            workspace_cache=cfg.workspace_cache,
            workspace_root=cfg.workspace_root,
            container_user=cfg.container_user,
        )
        self._store: WorkspaceStore = (
            MongoWorkspaceStore(cfg.mongo_url, cfg.mongo_db)
            if cfg.store_backend == "mongo"
            else LocalWorkspaceStore(root=cfg.workspace_cache)
        )
        self._watcher = Watcher(
            self._queue,
            dirty_ttl=cfg.dirty_ttl,
            workspace_cache=cfg.workspace_cache,
            workspace_cleanup_ttl=cfg.workspace_cleanup_ttl,
            workspace_cleanup_interval=cfg.workspace_cleanup_interval,
            workspace_store=self._store,
            file_manager=self._file_manager,
        )

    def start(self) -> None:
        """预取容器并启动守护线程。"""
        subprocess.run(
            ["docker", "network", "create", self._queue._network],
            capture_output=True, timeout=10,
        )
        self._queue.ensure_idle_count()
        self._watcher.start()

    def stop(self) -> None:
        """停止守护线程并清理所有容器（含追踪外的孤儿容器）。"""
        self._watcher.stop()
        for cid in list(self._queue.containers.keys()):
            try:
                self._queue.remove_container(cid)
            except Exception:
                pass
        # 通过可配置标签清理所有 worker 容器（含未被追踪的孤儿）
        role_filter = f"label={self._cfg.role_label}"
        try:
            result = subprocess.run(
                ["docker", "ps", "-a", "--filter", role_filter, "-q"],
                capture_output=True, text=True, timeout=10,
            )
            for cid in result.stdout.strip().split():
                subprocess.run(
                    ["docker", "rm", "-f", cid],
                    capture_output=True, timeout=10,
                )
        except Exception:
            pass

    def acquire(self, user_id: str, session_id: str) -> tuple[str, int]:
        """获取容器，返回 (container_id, fencing_token)。"""
        cid, token = self._scheduler.acquire(user_id, session_id)
        self._file_manager.pull(cid, user_id, session_id)
        return cid, token

    def release(self, container_id: str, user_id: str = "",
                session_id: str = "", fencing_token: int = 0) -> None:
        """释放容器（回写 workspace + 归还池，携带 fencing token）。"""
        self._file_manager.push(container_id, user_id, session_id)
        self._scheduler.release(container_id, fencing_token)

    def health_check(self) -> dict:
        return self._scheduler.health_check()

    def drain(self) -> int:
        """手动回收所有容器。"""
        recycled = 0
        for cid in list(self._queue.containers.keys()):
            if self._queue.recycle(cid):
                recycled += 1
        return recycled

    @property
    def queue(self) -> ContainerQueue:
        return self._queue

    @property
    def scheduler(self) -> Scheduler:
        return self._scheduler

    @property
    def file_manager(self) -> FileManager:
        return self._file_manager

    @property
    def watcher(self) -> Watcher:
        return self._watcher

    @property
    def store(self) -> WorkspaceStore:
        return self._store
