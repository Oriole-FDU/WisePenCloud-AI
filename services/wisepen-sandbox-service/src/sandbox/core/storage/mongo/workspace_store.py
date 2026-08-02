from __future__ import annotations

import asyncio
import base64
from typing import Any

from sandbox.core.storage.local.workspace_store import (
    content_bytes,
    normalize_relative_path,
    validate_workspace_id,
)
from sandbox.domain.entities import WorkspaceSnapshot, utc_now
from sandbox.domain.error_codes import SandboxErrorCode
from common.core.exceptions import ServiceException


class MongoWorkspaceStore:
    """Mongo implementation of the complete workspace snapshot contract."""

    def __init__(
        self,
        mongo_url: str = "mongodb://127.0.0.1:27017",
        db_name: str = "wisepen_sandbox",
        *,
        client: Any | None = None,
        max_files: int = 2000,
        max_file_bytes: int = 2 * 1024 * 1024,
        max_total_bytes: int = 64 * 1024 * 1024,
    ) -> None:
        if client is None:
            from pymongo import MongoClient

            client = MongoClient(mongo_url)
        self._collection = client[db_name]["workspace_snapshots"]
        self._max_files = max(1, max_files)
        self._max_file_bytes = max(1, max_file_bytes)
        self._max_total_bytes = max(1, max_total_bytes)
        try:
            self._collection.create_index(
                [("tenant_id", 1), ("workspace_id", 1)], unique=True
            )
        except AttributeError:
            # Small injected fakes used by unit tests need only find/replace semantics.
            pass

    async def snapshot(self, tenant_id: str, workspace_id: str) -> WorkspaceSnapshot:
        return await asyncio.to_thread(self._snapshot_sync, tenant_id, workspace_id)

    async def commit(
        self,
        snapshot: WorkspaceSnapshot,
        lease_id: str,
        fencing_token: int = 0,
    ) -> None:
        await asyncio.to_thread(self._commit_sync, snapshot, lease_id, fencing_token)

    async def delete(self, tenant_id: str, workspace_id: str) -> None:
        tenant_id = validate_workspace_id(tenant_id)
        workspace_id = validate_workspace_id(workspace_id)
        await asyncio.to_thread(
            self._collection.delete_one,
            {"tenant_id": tenant_id, "workspace_id": workspace_id},
        )

    def _snapshot_sync(self, tenant_id: str, workspace_id: str) -> WorkspaceSnapshot:
        tenant_id = validate_workspace_id(tenant_id)
        workspace_id = validate_workspace_id(workspace_id)
        document = self._collection.find_one(
            {"tenant_id": tenant_id, "workspace_id": workspace_id}
        )
        raw_files = document.get("files", {}) if document else {}
        files: dict[str, str | bytes] = {}
        for path, value in dict(raw_files).items():
            if isinstance(value, dict) and value.get("kind") == "binary":
                files[str(path)] = base64.b64decode(str(value.get("data", "")))
            elif isinstance(value, dict):
                files[str(path)] = str(value.get("data", ""))
            else:
                files[str(path)] = str(value)
        return WorkspaceSnapshot(tenant_id, workspace_id, files)

    def _commit_sync(
        self,
        snapshot: WorkspaceSnapshot,
        lease_id: str,
        fencing_token: int,
    ) -> None:
        tenant_id = validate_workspace_id(snapshot.tenant_id)
        workspace_id = validate_workspace_id(snapshot.workspace_id)
        if len(snapshot.files) > self._max_files:
            raise ServiceException(
                SandboxErrorCode.WORKSPACE_CACHE_LIMIT_EXCEEDED,
                "工作区缓存文件数量超出限制",
            )
        total_bytes = 0
        files: dict[str, dict[str, str]] = {}
        for path, content in snapshot.files.items():
            normalized = normalize_relative_path(path)
            raw = content_bytes(content)
            if len(raw) > self._max_file_bytes:
                raise ServiceException(
                    SandboxErrorCode.WORKSPACE_CACHE_LIMIT_EXCEEDED,
                    "工作区缓存单文件大小超出限制",
                )
            total_bytes += len(raw)
            if total_bytes > self._max_total_bytes:
                raise ServiceException(
                    SandboxErrorCode.WORKSPACE_CACHE_LIMIT_EXCEEDED,
                    "工作区缓存总大小超出限制",
                )
            if isinstance(content, bytes):
                files[normalized] = {
                    "kind": "binary",
                    "data": base64.b64encode(content).decode("ascii"),
                }
            else:
                files[normalized] = {"kind": "text", "data": content}
        existing = self._collection.find_one(
            {"tenant_id": tenant_id, "workspace_id": workspace_id},
            {"fencing_token": 1},
        )
        if existing and fencing_token < int(existing.get("fencing_token", 0)):
            raise ServiceException(
                SandboxErrorCode.FENCING_REJECTED,
                "工作区快照 fencing token 已过期",
            )
        document = {
                "tenant_id": tenant_id,
                "workspace_id": workspace_id,
                "files": files,
                "lease_id": lease_id,
                "fencing_token": fencing_token,
                "updated_at": utc_now(),
            }
        try:
            # The conditional replacement closes the race between the read above and
            # the write when multiple service instances checkpoint one workspace.
            self._collection.replace_one(
                {
                    "tenant_id": tenant_id,
                    "workspace_id": workspace_id,
                    "$or": [
                        {"fencing_token": {"$lte": fencing_token}},
                        {"fencing_token": {"$exists": False}},
                    ],
                },
                document,
                upsert=True,
            )
        except Exception as exc:
            # A stale conditional upsert conflicts with the unique workspace index.
            if exc.__class__.__name__ == "DuplicateKeyError":
                raise ServiceException(
                    SandboxErrorCode.FENCING_REJECTED,
                    "工作区快照 fencing token 已过期",
                ) from exc
            raise
