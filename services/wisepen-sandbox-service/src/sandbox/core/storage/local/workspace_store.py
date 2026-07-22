from __future__ import annotations

import json
import re
import shutil
import uuid
from pathlib import Path

from common.core.exceptions import ServiceException

from sandbox.domain.entities import WorkspaceSnapshot, utc_now
from sandbox.domain.error_codes import SandboxErrorCode


_ID = re.compile(r"^[A-Za-z0-9_-]{1,100}$")


def validate_workspace_id(value: str) -> str:
    # 租户和工作区标识会直接落到本地路径，必须限制成单段安全标识。
    if not _ID.fullmatch(value or ""):
        raise ServiceException(
            SandboxErrorCode.WORKSPACE_PATH_INVALID,
            "租户或工作区标识非法",
        )
    return value


def normalize_relative_path(value: str) -> str:
    # 缓存只接受工作区内相对路径，统一分隔符后拒绝绝对路径和目录穿越。
    path = (value or "").replace("\\", "/")
    candidate = Path(path)
    if not path or candidate.is_absolute() or any(part in ("", ".", "..") for part in candidate.parts):
        raise ServiceException(
            SandboxErrorCode.WORKSPACE_PATH_INVALID,
            "工作区路径必须是相对路径且不能穿越目录",
        )
    return "/".join(candidate.parts)


class LocalWorkspaceStore:
    """本地文件系统版工作区缓存。

    当前实现服务于开发和单实例部署：commit 写入完整快照，snapshot 在下一次
    allocate 前读回同一 tenant/workspace 的缓存。生产环境可用对象存储实现同一端口。
    """

    def __init__(
        self,
        root: str = "/tmp/wisepen-workspaces",
        *,
        max_files: int = 2000,
        max_file_bytes: int = 2 * 1024 * 1024,
        max_total_bytes: int = 64 * 1024 * 1024,
        manifest_name: str = ".wisepen-workspace-manifest.json",
    ) -> None:
        self._root = Path(root).resolve()
        self._staging_root = self._root.parent / f"{self._root.name}.staging"
        self._max_files = max(1, max_files)
        self._max_file_bytes = max(1, max_file_bytes)
        self._max_total_bytes = max(1, max_total_bytes)
        self._manifest_name = normalize_relative_path(manifest_name)
        if "/" in self._manifest_name:
            raise ServiceException(
                SandboxErrorCode.WORKSPACE_PATH_INVALID,
                "工作区清单文件名不能包含目录",
            )

    def _path(self, tenant_id: str, workspace_id: str) -> Path:
        validate_workspace_id(tenant_id)
        validate_workspace_id(workspace_id)
        return self._root / tenant_id / workspace_id

    def _staging_path(self, tenant_id: str, workspace_id: str) -> Path:
        validate_workspace_id(tenant_id)
        validate_workspace_id(workspace_id)
        # 暂存目录带随机后缀，避免并发/失败重试复用同一个临时目录。
        return self._staging_root / tenant_id / f"{workspace_id}-{uuid.uuid4().hex}"

    def _validate_limits(self, files: dict[str, str]) -> tuple[int, int]:
        # 提交前先按 UTF-8 文本大小计算配额，防止缓存目录被用户输出撑爆。
        if len(files) > self._max_files:
            raise ServiceException(
                SandboxErrorCode.WORKSPACE_CACHE_LIMIT_EXCEEDED,
                "工作区缓存文件数量超出限制",
            )
        total_bytes = 0
        for relative, content in files.items():
            normalize_relative_path(relative)
            size = len(content.encode("utf-8"))
            if size > self._max_file_bytes:
                raise ServiceException(
                    SandboxErrorCode.WORKSPACE_CACHE_LIMIT_EXCEEDED,
                    "工作区缓存单文件大小超出限制",
                )
            total_bytes += size
            if total_bytes > self._max_total_bytes:
                raise ServiceException(
                    SandboxErrorCode.WORKSPACE_CACHE_LIMIT_EXCEEDED,
                    "工作区缓存总大小超出限制",
                )
        return len(files), total_bytes

    def _validate_disk_file(
        self,
        path: Path,
        file_count: int,
        total_bytes: int,
    ) -> tuple[int, int]:
        # 读取历史缓存时也执行同一套配额，避免旧版本或手工文件绕过限制。
        file_count += 1
        if file_count > self._max_files:
            raise ServiceException(
                SandboxErrorCode.WORKSPACE_CACHE_LIMIT_EXCEEDED,
                "工作区缓存文件数量超出限制",
            )
        size = path.stat().st_size
        if size > self._max_file_bytes:
            raise ServiceException(
                SandboxErrorCode.WORKSPACE_CACHE_LIMIT_EXCEEDED,
                "工作区缓存单文件大小超出限制",
            )
        total_bytes += size
        if total_bytes > self._max_total_bytes:
            raise ServiceException(
                SandboxErrorCode.WORKSPACE_CACHE_LIMIT_EXCEEDED,
                "工作区缓存总大小超出限制",
            )
        return file_count, total_bytes

    def _write_snapshot_to_dir(
        self,
        target: Path,
        snapshot: WorkspaceSnapshot,
        lease_id: str,
        fencing_token: int,
    ) -> None:
        file_count, total_bytes = self._validate_limits(snapshot.files)
        target.mkdir(parents=True, exist_ok=True)
        for relative, content in snapshot.files.items():
            normalized = normalize_relative_path(relative)
            # 清单文件是系统元数据，用户文件不能覆盖它，否则会破坏缓存审计信息。
            if normalized == self._manifest_name:
                raise ServiceException(
                    SandboxErrorCode.WORKSPACE_PATH_INVALID,
                    "工作区清单文件为系统保留路径",
                )
            path = target / normalized
            path.parent.mkdir(parents=True, exist_ok=True)
            # 目录由本进程创建，但仍在写入前检查符号链接，防止残留 staging 被利用。
            if path.is_symlink():
                raise ServiceException(
                    SandboxErrorCode.WORKSPACE_PATH_INVALID,
                    "工作区不允许包含符号链接",
                )
            path.write_text(content, encoding="utf-8")
        manifest = {
            "tenant_id": snapshot.tenant_id,
            "workspace_id": snapshot.workspace_id,
            "lease_id": lease_id,
            "fencing_token": fencing_token,
            "committed_at": utc_now().isoformat(),
            "file_count": file_count,
            "total_bytes": total_bytes,
        }
        (target / self._manifest_name).write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    @staticmethod
    def _cleanup_dir(path: Path) -> None:
        if path.is_symlink() or path.is_file():
            path.unlink()
        elif path.exists():
            shutil.rmtree(path)

    def _replace_workspace_dir(self, root: Path, staging: Path) -> None:
        backup = root.parent / f".{root.name}.backup-{uuid.uuid4().hex}"
        try:
            if root.exists():
                if root.is_symlink():
                    raise ServiceException(
                        SandboxErrorCode.WORKSPACE_PATH_INVALID,
                        "工作区根目录不能是符号链接",
                    )
                root.rename(backup)
            # 使用同盘 rename 安装 staging，使工作区缓存要么保持旧版本，要么切到新版本。
            staging.rename(root)
        except Exception:
            # 新版本安装失败时回滚旧缓存；销毁链路会继续销毁沙箱并上报同步失败。
            if root.exists():
                self._cleanup_dir(root)
            if backup.exists():
                backup.rename(root)
            raise
        try:
            self._cleanup_dir(backup)
        except Exception:
            # 新缓存已经安装成功，旧备份清理失败不应反向影响本次 commit 结果。
            pass

    async def snapshot(self, tenant_id: str, workspace_id: str) -> WorkspaceSnapshot:
        root = self._path(tenant_id, workspace_id)
        files: dict[str, str] = {}
        file_count = 0
        total_bytes = 0
        if root.exists():
            if root.is_symlink():
                raise ServiceException(
                    SandboxErrorCode.WORKSPACE_PATH_INVALID,
                    "工作区根目录不能是符号链接",
                )
            for path in root.rglob("*"):
                if path.is_symlink():
                    raise ServiceException(
                        SandboxErrorCode.WORKSPACE_PATH_INVALID,
                        "工作区不允许包含符号链接",
                    )
                if path.is_file():
                    relative = normalize_relative_path(str(path.relative_to(root)))
                    # 清单文件只用于本地审计，不还原到用户沙箱。
                    if relative == self._manifest_name:
                        continue
                    file_count, total_bytes = self._validate_disk_file(
                        path,
                        file_count,
                        total_bytes,
                    )
                    content = path.read_text(encoding="utf-8", errors="replace")
                    files[relative] = content
        return WorkspaceSnapshot(tenant_id, workspace_id, files)

    async def commit(
        self,
        snapshot: WorkspaceSnapshot,
        lease_id: str,
        fencing_token: int = 0,
    ) -> None:
        root = self._path(snapshot.tenant_id, snapshot.workspace_id)
        staging = self._staging_path(snapshot.tenant_id, snapshot.workspace_id)
        self._cleanup_dir(staging)
        try:
            self._write_snapshot_to_dir(staging, snapshot, lease_id, fencing_token)
            root.parent.mkdir(parents=True, exist_ok=True)
            self._replace_workspace_dir(root, staging)
        finally:
            self._cleanup_dir(staging)
