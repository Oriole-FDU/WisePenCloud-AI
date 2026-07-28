"""
MongoWorkspaceStore — MongoDB 工作空间持久化。

每个文件存储为 workspace_files 集合中的一个 document。
会话元信息存储在 workspace_sessions 集合中。
"""
from __future__ import annotations

import time
from datetime import datetime, timezone

from sandbox.Queue.store.interface import (
    WorkspaceStore, WorkspaceFile, WorkspaceSnapshot,
)

_COLL_FILES = "workspace_files"
_COLL_SESSIONS = "workspace_sessions"


class MongoWorkspaceStore(WorkspaceStore):
    """基于 MongoDB 的工作空间持久化。"""

    def __init__(self, mongo_url: str = "mongodb://127.0.0.1:27017",
                 db_name: str = "wisepen_sandbox"):
        from pymongo import MongoClient
        self._client = MongoClient(mongo_url)
        self._db = self._client[db_name]
        self._files = self._db[_COLL_FILES]
        self._sessions = self._db[_COLL_SESSIONS]

    def save(self, user_id: str, session_id: str,
             files: list[WorkspaceFile], version: int = 0) -> int:
        new_version = version + 1
        now = time.time()

        # 更新会话元信息
        self._sessions.update_one(
            {"user_id": user_id, "session_id": session_id},
            {"$set": {
                "version": new_version,
                "file_count": len(files),
                "updated_at": now,
            }},
            upsert=True,
        )

        # 批量 upsert 文件
        from pymongo import UpdateOne
        ops = []
        for f in files:
            ops.append(UpdateOne(
                {"user_id": user_id, "session_id": session_id, "path": f.path},
                {"$set": {
                    "content": f.content,
                    "encoding": f.encoding,
                    "size": f.size or len(f.content.encode(f.encoding)),
                    "updated_at": now,
                }},
                upsert=True,
            ))
        if ops:
            self._files.bulk_write(ops)

        return new_version

    def load(self, user_id: str, session_id: str) -> WorkspaceSnapshot:
        session = self._sessions.find_one(
            {"user_id": user_id, "session_id": session_id},
        )
        version = session["version"] if session else 0
        updated_at = session.get("updated_at", 0.0) if session else 0.0

        files: list[WorkspaceFile] = []
        for doc in self._files.find(
            {"user_id": user_id, "session_id": session_id},
        ):
            files.append(WorkspaceFile(
                path=doc["path"],
                content=doc.get("content", ""),
                encoding=doc.get("encoding", "utf-8"),
                size=doc.get("size", 0),
            ))

        return WorkspaceSnapshot(
            user_id=user_id, session_id=session_id,
            files=files, version=version, updated_at=updated_at,
        )

    def delete(self, user_id: str, session_id: str) -> None:
        self._files.delete_many(
            {"user_id": user_id, "session_id": session_id},
        )
        self._sessions.delete_one(
            {"user_id": user_id, "session_id": session_id},
        )

    def list_sessions(self, user_id: str) -> list[str]:
        return [
            doc["session_id"]
            for doc in self._sessions.find(
                {"user_id": user_id},
                {"session_id": 1},
            )
        ]

    def list_all_stale(self, older_than: float) -> list[tuple[str, str]]:
        cutoff = datetime.fromtimestamp(time.time() - older_than, tz=timezone.utc)
        result: list[tuple[str, str]] = []
        for doc in self._sessions.find(
            {"updated_at": {"$lt": cutoff.timestamp()}},
            {"user_id": 1, "session_id": 1},
        ):
            result.append((doc["user_id"], doc["session_id"]))
        return result
