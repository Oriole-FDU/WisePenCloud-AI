"""
LocalWorkspaceStore — 本地文件系统实现。

文件存储在 {root}/{user_id}/{session_id}/ 下。
与当前 FileManager 的 host_path 格式完全兼容。
"""
from __future__ import annotations

import os
import time
from dataclasses import asdict

from sandbox.Queue.workspace_store import (
    WorkspaceStore, WorkspaceFile, WorkspaceSnapshot,
)


class LocalWorkspaceStore(WorkspaceStore):
    """基于本地文件系统的工作空间持久化。"""

    def __init__(self, root: str = "/workspaces"):
        self._root = root

    def _dir(self, user_id: str, session_id: str) -> str:
        return os.path.join(self._root, user_id, session_id)

    def save(self, user_id: str, session_id: str,
             files: list[WorkspaceFile], version: int = 0) -> int:
        d = self._dir(user_id, session_id)
        os.makedirs(d, exist_ok=True)
        for f in files:
            fp = os.path.join(d, os.path.normpath(f.path.lstrip("/")))
            os.makedirs(os.path.dirname(fp), exist_ok=True)
            with open(fp, "w", encoding=f.encoding) as fh:
                fh.write(f.content)
        new_version = version + 1
        # 写入版本标记
        with open(os.path.join(d, ".version"), "w") as fh:
            fh.write(str(new_version))
        return new_version

    def load(self, user_id: str, session_id: str) -> WorkspaceSnapshot:
        d = self._dir(user_id, session_id)
        if not os.path.isdir(d):
            return WorkspaceSnapshot(user_id=user_id, session_id=session_id)

        version = 0
        vf = os.path.join(d, ".version")
        if os.path.isfile(vf):
            with open(vf) as fh:
                version = int(fh.read().strip())

        files: list[WorkspaceFile] = []
        for root, _, filenames in os.walk(d):
            for name in filenames:
                if name == ".version":
                    continue
                fp = os.path.join(root, name)
                rel = os.path.relpath(fp, d).replace("\\", "/")
                try:
                    with open(fp, "r", encoding="utf-8") as fh:
                        content = fh.read()
                except Exception:
                    continue
                files.append(WorkspaceFile(
                    path=rel, content=content,
                    encoding="utf-8", size=os.path.getsize(fp),
                ))
        mtime = os.path.getmtime(d) if files else time.time()
        return WorkspaceSnapshot(
            user_id=user_id, session_id=session_id,
            files=files, version=version, updated_at=mtime,
        )

    def delete(self, user_id: str, session_id: str) -> None:
        import shutil
        d = self._dir(user_id, session_id)
        if os.path.isdir(d):
            shutil.rmtree(d, ignore_errors=True)

    def list_sessions(self, user_id: str) -> list[str]:
        d = os.path.join(self._root, user_id)
        if not os.path.isdir(d):
            return []
        return [name for name in os.listdir(d)
                if os.path.isdir(os.path.join(d, name))]

    def list_all_stale(self, older_than: float) -> list[tuple[str, str]]:
        result: list[tuple[str, str]] = []
        if not os.path.isdir(self._root):
            return result
        now = time.time()
        for uid in os.listdir(self._root):
            ud = os.path.join(self._root, uid)
            if not os.path.isdir(ud):
                continue
            for sid in os.listdir(ud):
                sd = os.path.join(ud, sid)
                if not os.path.isdir(sd):
                    continue
                try:
                    mtime = os.path.getmtime(sd)
                    if now - mtime > older_than:
                        result.append((uid, sid))
                except OSError:
                    pass
        return result
