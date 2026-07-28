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
from common.sandbox import SandboxErrorCode
from sandbox.Queue.store.interface import WorkspaceStore, WorkspaceFile


class FileManager:
    """Host <-> container workspace file sync.  Optional WorkspaceStore for dual persistence."""

    def __init__(self, workspace_cache: str = "/workspaces",
                 store: WorkspaceStore | None = None,
                 workspace_root: str = "/home/gem/workspaces",
                 container_user: str = "gem:gem"):
        self._workspace_cache = workspace_cache
        self._store = store
        self._workspace_root = workspace_root
        self._container_user = container_user
        self._last_checkpoint: dict[tuple[str, str], float] = {}
        self._checkpoint_interval: float = 300.0

    def host_path(self, user_id: str, session_id: str) -> str:
        return f"{self._workspace_cache}/{user_id}/{session_id}"

    def container_path(self, user_id: str, session_id: str) -> str:
        """Tenant-scoped physical path on the AIO container."""
        return f"{self._workspace_root}/{user_id}/{session_id}"

    # ---- acquire / release ----

    def pull(self, container_id: str, user_id: str, session_id: str) -> None:
        """Restore workspace from Store → host cache → docker cp to container + chown."""
        host = self.host_path(user_id, session_id)
        os.makedirs(host, exist_ok=True)

        # Step 1: Store → host cache
        if self._store:
            snapshot = self._store.load(user_id, session_id)
            if snapshot.files:
                for f in snapshot.files:
                    fp = os.path.join(host, os.path.normpath(f.path.lstrip("/")))
                    os.makedirs(os.path.dirname(fp), exist_ok=True)
                    with open(fp, "w", encoding=f.encoding) as fh:
                        fh.write(f.content)

        # Step 2: host cache → container (tenant-scoped physical path)
        if os.path.exists(host) and os.listdir(host):
            ct_path = self.container_path(user_id, session_id)
            # Ensure parent directories exist on container (docker cp may not create them)
            subprocess.run(
                ["docker", "exec", container_id,
                 "mkdir", "-p", ct_path],
                capture_output=True, timeout=10,
            )
            self._docker_cp(f"{host}/.", f"{container_id}:{ct_path}/")
            # docker cp creates files as root; chown to container user for AIO access
            subprocess.run(
                ["docker", "exec", container_id,
                 "chown", "-R", self._container_user, ct_path],
                capture_output=True, timeout=10,
            )

    def push(self, container_id: str, user_id: str, session_id: str) -> None:
        """Container → host cache → Store."""
        host = self.host_path(user_id, session_id)
        os.makedirs(host, exist_ok=True)

        # Step 1: container → host cache
        ct_path = self.container_path(user_id, session_id)
        try:
            self._docker_cp(f"{container_id}:{ct_path}/.", f"{host}/")
        except ServiceException:
            pass  # non-fatal: container may not have workspace yet

        # Step 2: host cache → Store
        if self._store:
            self._save_to_store(user_id, session_id, host)

        self._last_checkpoint.pop((user_id, session_id), None)

    # ---- checkpoint (periodic auto-save without release) ----

    def checkpoint(self, container_id: str, user_id: str, session_id: str) -> bool:
        """Container → host cache + Store."""
        host = self.host_path(user_id, session_id)
        os.makedirs(host, exist_ok=True)

        ct_path = self.container_path(user_id, session_id)
        try:
            self._docker_cp(f"{container_id}:{ct_path}/.", f"{host}/")
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
