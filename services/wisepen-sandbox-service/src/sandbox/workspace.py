from __future__ import annotations

from pathlib import Path

from sandbox.models import WorkspaceSnapshot


class LocalWorkspaceStore:
    def __init__(self, root: str = "/tmp/wisepen-workspaces") -> None:
        self._root = Path(root)

    def _path(self, tenant_id: str, workspace_id: str) -> Path:
        return self._root / tenant_id / workspace_id

    async def snapshot(self, tenant_id: str, workspace_id: str) -> WorkspaceSnapshot:
        root = self._path(tenant_id, workspace_id)
        files: dict[str, str] = {}
        if root.exists():
            for path in root.rglob("*"):
                if path.is_file():
                    files[str(path.relative_to(root))] = path.read_text(
                        encoding="utf-8", errors="replace"
                    )
        return WorkspaceSnapshot(tenant_id, workspace_id, files)

    async def commit(self, snapshot: WorkspaceSnapshot, lease_id: str) -> None:
        root = self._path(snapshot.tenant_id, snapshot.workspace_id)
        root.mkdir(parents=True, exist_ok=True)
        for relative, content in snapshot.files.items():
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
