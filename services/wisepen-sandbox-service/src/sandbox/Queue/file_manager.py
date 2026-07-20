"""
Host/container workspace file synchronization via docker cp + optional store.

FileManager bridges the host filesystem (workspace cache) and container
filesystem (AIO sandbox's /workspace/).

Lifecycle:
  pull(host→container) on acquire  — restore tenant workspace into container
  push(container→host) on release  — persist container writes back to host
  checkpoint(container→host+store) — periodic save without release (auto-save)
"""
from __future__ import annotations

import os
import subprocess
import time

from common.core.exceptions import ServiceException
from sandbox.Queue.store.interface import WorkspaceStore, WorkspaceFile


class FileManager:
    """Host <-> container workspace file sync.  Optional WorkspaceStore for dual persistence."""

    def __init__(self, workspace_cache: str = "/workspaces",
                 store: WorkspaceStore | None = None):
        self._workspace_cache = workspace_cache
        self._store = store
        # checkpoint tracking
        self._last_checkpoint: dict[tuple[str, str], float] = {}  # (uid,sid) → epoch
        self._checkpoint_interval: float = 300.0  # 5 minutes

    def host_path(self, user_id: str, session_id: str) -> str:
        return f"{self._workspace_cache}/{user_id}/{session_id}"

    # ---- acquire / release ----

    def pull(self, container_id: str, user_id: str, session_id: str) -> None:
        """Restore workspace from store (if available) → host cache → docker cp to container."""
        host = self.host_path(user_id, session_id)
        os.makedirs(host, exist_ok=True)

        # Step 1: 从 Store 恢复到主机缓存
        if self._store:
            snapshot = self._store.load(user_id, session_id)
            if snapshot.files:
                for f in snapshot.files:
                    fp = os.path.join(host, os.path.normpath(f.path.lstrip("/")))
                    os.makedirs(os.path.dirname(fp), exist_ok=True)
                    with open(fp, "w", encoding=f.encoding) as fh:
                        fh.write(f.content)

        # Step 2: 主机缓存 → 容器
        if os.listdir(host):
            self._docker_cp(f"{host}/.", f"{container_id}:/workspace/")

    def push(self, container_id: str, user_id: str, session_id: str) -> None:
        """容器 → 主机缓存 → Store (双写持久化)。"""
        host = self.host_path(user_id, session_id)
        os.makedirs(host, exist_ok=True)

        # Step 1: 容器 → 主机缓存
        try:
            self._docker_cp(f"{container_id}:/workspace/.", f"{host}/")
        except ServiceException:
            pass  # non-fatal: container will be recycled

        # Step 2: 主机缓存 → Store
        if self._store:
            self._save_to_store(user_id, session_id, host)

        # 清理检查点记录
        self._last_checkpoint.pop((user_id, session_id), None)

    # ---- checkpoint (periodic auto-save without release) ----

    def checkpoint(self, container_id: str, user_id: str, session_id: str) -> bool:
        """将容器工作空间增量保存到主机缓存 + Store。不同步时不写 Store。"""
        host = self.host_path(user_id, session_id)
        os.makedirs(host, exist_ok=True)

        try:
            self._docker_cp(f"{container_id}:/workspace/.", f"{host}/")
        except ServiceException:
            return False

        if self._store:
            self._save_to_store(user_id, session_id, host)

        self._last_checkpoint[(user_id, session_id)] = time.time()
        return True

    def should_checkpoint(self, user_id: str, session_id: str) -> bool:
        """是否到达检查点间隔。"""
        last = self._last_checkpoint.get((user_id, session_id), 0.0)
        return time.time() - last >= self._checkpoint_interval

    # ---- internal ----

    def _save_to_store(self, user_id: str, session_id: str, host_dir: str) -> None:
        """从主机目录读取所有文件并写入 Store。"""
        files: list[WorkspaceFile] = []
        for root, _, filenames in os.walk(host_dir):
            for name in filenames:
                fp = os.path.join(root, name)
                rel = os.path.relpath(fp, host_dir).replace("\\", "/")
                try:
                    with open(fp, "r", encoding="utf-8") as fh:
                        content = fh.read()
                except Exception:
                    continue
                files.append(WorkspaceFile(
                    path=rel, content=content,
                    encoding="utf-8", size=len(content.encode("utf-8")),
                ))
        if files:
            self._store.save(user_id, session_id, files)

    @staticmethod
    def _docker_cp(source: str, dest: str) -> None:
        completed = subprocess.run(
            ["docker", "cp", source, dest],
            capture_output=True, text=True, timeout=30,
        )
        if completed.returncode != 0:
            raise ServiceException(SandboxErrorCode.FILE_SYNC_FAILED, completed.stderr.strip()[:500])
