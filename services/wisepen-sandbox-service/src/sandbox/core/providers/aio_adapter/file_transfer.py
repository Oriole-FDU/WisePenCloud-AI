from __future__ import annotations

import asyncio
import os
import re
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import Sequence

from common.core.exceptions import ServiceException

from sandbox.domain.entities import SandboxRef, WorkspaceSnapshot
from sandbox.domain.error_codes import SandboxErrorCode

_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,100}$")


class DockerWorkspaceTransfer:
    """Move complete workspace snapshots through docker cp without exposing Docker upstream."""

    def __init__(
        self,
        *,
        docker_bin: str = "docker",
        workspace_root: str = "/home/gem/workspaces",
        container_user: str = "gem:gem",
        command_timeout_seconds: float = 30.0,
        max_files: int = 2000,
        max_file_bytes: int = 2 * 1024 * 1024,
        max_total_bytes: int = 64 * 1024 * 1024,
        runner=subprocess.run,
    ) -> None:
        self._docker_bin = docker_bin
        self._workspace_root = workspace_root.rstrip("/")
        self._container_user = container_user
        self._timeout = command_timeout_seconds
        self._max_files = max(1, max_files)
        self._max_file_bytes = max(1, max_file_bytes)
        self._max_total_bytes = max(1, max_total_bytes)
        self._runner = runner

    async def copy_in(self, sandbox: SandboxRef, snapshot: WorkspaceSnapshot) -> None:
        await asyncio.to_thread(self._copy_in_sync, sandbox, snapshot)

    async def copy_out(
        self, sandbox: SandboxRef, tenant_id: str, workspace_id: str
    ) -> WorkspaceSnapshot:
        return await asyncio.to_thread(
            self._copy_out_sync, sandbox, tenant_id, workspace_id
        )

    async def checkpoint(
        self,
        sandbox: SandboxRef,
        tenant_id: str,
        workspace_id: str,
        lease_id: str,
        fencing_token: int,
    ) -> WorkspaceSnapshot:
        if not lease_id or fencing_token <= 0:
            raise ServiceException(
                SandboxErrorCode.FENCING_REJECTED,
                "checkpoint 需要有效租约和 fencing token",
            )
        return await self.copy_out(sandbox, tenant_id, workspace_id)

    def _copy_in_sync(self, sandbox: SandboxRef, snapshot: WorkspaceSnapshot) -> None:
        tenant_id = self._safe_id(snapshot.tenant_id)
        workspace_id = self._safe_id(snapshot.workspace_id)
        current = self._container_path(tenant_id, workspace_id)
        suffix = uuid.uuid4().hex
        staging_container = f"{current}.staging-{suffix}"
        backup_container = f"{current}.backup-{suffix}"
        with tempfile.TemporaryDirectory(prefix="wisepen-copy-in-") as staging:
            staging_path = Path(staging)
            self._write_staging(staging_path, snapshot)
            self._run(["exec", sandbox.provider_id, "rm", "-rf", staging_container, backup_container])
            self._run(["exec", sandbox.provider_id, "mkdir", "-p", staging_container])
            try:
                if snapshot.files:
                    self._run(
                        ["cp", f"{staging_path}/.", f"{sandbox.provider_id}:{staging_container}/"]
                    )
                self._run(
                    ["exec", sandbox.provider_id, "chown", "-R", self._container_user, staging_container]
                )
                swap_script = (
                    'set -eu; current="$1"; staging="$2"; backup="$3"; '
                    'if [ -e "$current" ]; then mv "$current" "$backup"; fi; '
                    'if mv "$staging" "$current"; then rm -rf "$backup"; '
                    'else if [ -e "$backup" ]; then mv "$backup" "$current"; fi; exit 1; fi'
                )
                self._run(
                    [
                        "exec",
                        sandbox.provider_id,
                        "sh",
                        "-c",
                        swap_script,
                        "sh",
                        current,
                        staging_container,
                        backup_container,
                    ]
                )
            except Exception:
                self._run_cleanup(
                    ["exec", sandbox.provider_id, "rm", "-rf", staging_container, backup_container]
                )
                raise

    def _write_staging(self, staging: Path, snapshot: WorkspaceSnapshot) -> None:
        if len(snapshot.files) > self._max_files:
            self._limit_error("工作区文件数量超出限制")
        total_bytes = 0
        for relative, content in snapshot.files.items():
            target = staging / self._safe_relative(relative)
            target.parent.mkdir(parents=True, exist_ok=True)
            raw = content if isinstance(content, bytes) else content.encode("utf-8")
            if len(raw) > self._max_file_bytes:
                self._limit_error("工作区单文件大小超出限制")
            total_bytes += len(raw)
            if total_bytes > self._max_total_bytes:
                self._limit_error("工作区总大小超出限制")
            target.write_bytes(raw)

    def _copy_out_sync(
        self, sandbox: SandboxRef, tenant_id: str, workspace_id: str
    ) -> WorkspaceSnapshot:
        tenant_id = self._safe_id(tenant_id)
        workspace_id = self._safe_id(workspace_id)
        container_path = self._container_path(tenant_id, workspace_id)
        existence = self._run_result(
            ["exec", sandbox.provider_id, "test", "-d", container_path]
        )
        if existence.returncode == 1 and not (existence.stderr or existence.stdout):
            return WorkspaceSnapshot(tenant_id, workspace_id)
        self._raise_for_result(existence, ["exec", sandbox.provider_id, "test", "-d", container_path])

        with tempfile.TemporaryDirectory(prefix="wisepen-copy-out-") as staging:
            destination = Path(staging)
            self._run(["cp", f"{sandbox.provider_id}:{container_path}/.", f"{destination}/"])
            files: dict[str, str | bytes] = {}
            total_bytes = 0
            for root, dirnames, filenames in os.walk(destination, followlinks=False):
                root_path = Path(root)
                for name in dirnames:
                    if (root_path / name).is_symlink():
                        self._path_error("工作区不允许包含符号链接")
                for name in filenames:
                    path = root_path / name
                    if path.is_symlink():
                        self._path_error("工作区不允许包含符号链接")
                    relative = path.relative_to(destination).as_posix()
                    if relative == ".wisepen-workspace-manifest.json":
                        continue
                    normalized = self._safe_relative(relative).as_posix()
                    raw = path.read_bytes()
                    if len(raw) > self._max_file_bytes:
                        self._limit_error("工作区单文件大小超出限制")
                    total_bytes += len(raw)
                    if len(files) + 1 > self._max_files:
                        self._limit_error("工作区文件数量超出限制")
                    if total_bytes > self._max_total_bytes:
                        self._limit_error("工作区总大小超出限制")
                    try:
                        files[normalized] = raw.decode("utf-8")
                    except UnicodeDecodeError:
                        files[normalized] = raw
            return WorkspaceSnapshot(tenant_id, workspace_id, files)

    def _container_path(self, tenant_id: str, workspace_id: str) -> str:
        return f"{self._workspace_root}/{tenant_id}/{workspace_id}"

    @staticmethod
    def _safe_relative(value: str) -> Path:
        normalized = (value or "").replace("\\", "/")
        path = Path(normalized)
        if not normalized or path.is_absolute() or any(
            part in ("", ".", "..") for part in path.parts
        ):
            DockerWorkspaceTransfer._path_error("工作区路径必须是安全的相对路径")
        return path

    def _run(self, args: list[str]) -> str:
        result = self._run_result(args)
        self._raise_for_result(result, args)
        return result.stdout or ""

    def _run_result(self, args: Sequence[str]):
        try:
            return self._runner(
                [self._docker_bin, *args],
                capture_output=True,
                text=True,
                timeout=self._timeout,
                check=False,
            )
        except (FileNotFoundError, OSError, subprocess.TimeoutExpired) as exc:
            raise ServiceException(
                SandboxErrorCode.WORKSPACE_SYNC_FAILED,
                "docker workspace transfer command failed",
            ) from exc

    def _raise_for_result(self, result, args: Sequence[str]) -> None:
        if result.returncode == 0:
            return
        detail = (result.stderr or result.stdout or "").strip()
        raise ServiceException(
            SandboxErrorCode.WORKSPACE_SYNC_FAILED,
            f"docker workspace transfer failed ({' '.join(args[:2])}): {detail[:500]}",
        )

    def _run_cleanup(self, args: list[str]) -> None:
        try:
            self._run_result(args)
        except ServiceException:
            pass

    @staticmethod
    def _safe_id(value: str) -> str:
        if not _SAFE_ID.fullmatch(value or ""):
            DockerWorkspaceTransfer._path_error("租户或工作区标识非法")
        return value

    @staticmethod
    def _path_error(message: str) -> None:
        raise ServiceException(SandboxErrorCode.WORKSPACE_PATH_INVALID, message)

    @staticmethod
    def _limit_error(message: str) -> None:
        raise ServiceException(SandboxErrorCode.WORKSPACE_CACHE_LIMIT_EXCEEDED, message)
