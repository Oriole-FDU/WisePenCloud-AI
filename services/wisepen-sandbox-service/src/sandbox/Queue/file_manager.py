"""
Host/container workspace file synchronization via docker cp.

FileManager bridges the host filesystem (workspace cache) and container
filesystem (AIO sandbox's /workspace/).

Lifecycle:
  pull(host→container) on acquire  — restore tenant workspace into container
  push(container→host) on release  — persist container writes back to host
"""
from __future__ import annotations

import os
import subprocess

from common.sandbox import SandboxException


class FileManager:
    """Host <-> container workspace file sync for AIO sandbox containers."""

    def __init__(self, workspace_cache: str = "/workspaces") -> None:
        self._workspace_cache = workspace_cache

    def host_path(self, user_id: str, session_id: str) -> str:
        return f"{self._workspace_cache}/{user_id}/{session_id}"

    def pull(self, container_id: str, user_id: str, session_id: str) -> None:
        """Copy workspace from host cache into container on allocation."""
        host = self.host_path(user_id, session_id)
        if not os.path.isdir(host):
            os.makedirs(host, exist_ok=True)
            return  # empty workspace — nothing to copy
        self._docker_cp(f"{host}/.", f"{container_id}:/workspace/")

    def push(self, container_id: str, user_id: str, session_id: str) -> None:
        """Copy workspace from container back to host cache on release."""
        host = self.host_path(user_id, session_id)
        os.makedirs(host, exist_ok=True)
        try:
            self._docker_cp(f"{container_id}:/workspace/.", f"{host}/")
        except SandboxException:
            pass  # non-fatal: container will be recycled, partial data acceptable

    @staticmethod
    def _docker_cp(source: str, dest: str) -> None:
        completed = subprocess.run(
            ["docker", "cp", source, dest],
            capture_output=True, text=True, timeout=30,
        )
        if completed.returncode != 0:
            raise SandboxException.file_sync_failed(completed.stderr.strip()[:500])
