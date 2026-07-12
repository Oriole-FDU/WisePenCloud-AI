from __future__ import annotations

import re
from pathlib import Path

from sandbox.errors import SandboxDomainError
from sandbox.models import WorkspaceSnapshot


class WorkspacePathError(SandboxDomainError):
    code = "WORKSPACE_PATH_INVALID"


_ID = re.compile(r"^[A-Za-z0-9_-]{1,100}$")


def validate_workspace_id(value: str) -> str:
    if not _ID.fullmatch(value or ""):
        raise WorkspacePathError("invalid tenant or workspace identifier")
    return value


def normalize_relative_path(value: str) -> str:
    path = (value or "").replace("\\", "/")
    candidate = Path(path)
    if not path or candidate.is_absolute() or any(part in ("", ".", "..") for part in candidate.parts):
        raise WorkspacePathError("workspace paths must be relative and cannot traverse")
    return "/".join(candidate.parts)


class LocalWorkspaceStore:
    def __init__(self, root: str = "/tmp/wisepen-workspaces") -> None:
        self._root = Path(root).resolve()

    def _path(self, tenant_id: str, workspace_id: str) -> Path:
        validate_workspace_id(tenant_id)
        validate_workspace_id(workspace_id)
        return self._root / tenant_id / workspace_id

    async def snapshot(self, tenant_id: str, workspace_id: str) -> WorkspaceSnapshot:
        root = self._path(tenant_id, workspace_id)
        files: dict[str, str] = {}
        if root.exists():
            if root.is_symlink():
                raise WorkspacePathError("workspace root cannot be a symlink")
            for path in root.rglob("*"):
                if path.is_symlink():
                    raise WorkspacePathError("workspace symlinks are not allowed")
                if path.is_file():
                    files[normalize_relative_path(str(path.relative_to(root)))] = path.read_text(
                        encoding="utf-8", errors="replace"
                    )
        return WorkspaceSnapshot(tenant_id, workspace_id, files)

    async def commit(
        self,
        snapshot: WorkspaceSnapshot,
        lease_id: str,
        fencing_token: int = 0,
    ) -> None:
        root = self._path(snapshot.tenant_id, snapshot.workspace_id)
        root.mkdir(parents=True, exist_ok=True)
        for relative in snapshot.deleted_files:
            path = root / normalize_relative_path(relative)
            if path.exists() and not path.is_symlink():
                path.unlink()
        for relative, content in snapshot.files.items():
            path = root / normalize_relative_path(relative)
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.is_symlink():
                raise WorkspacePathError("workspace symlinks are not allowed")
            path.write_text(content, encoding="utf-8")
