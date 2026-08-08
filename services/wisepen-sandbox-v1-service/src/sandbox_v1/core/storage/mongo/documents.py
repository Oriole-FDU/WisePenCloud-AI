from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sandbox_v1.domain.entities import (
    Endpoint,
    SandboxRecord,
    SandboxRef,
    SandboxState,
    UserSandboxBindingRecord,
    WorkspaceRecord,
    WorkspaceSnapshotRef,
    WorkspaceState,
)


def _datetime(value: Any) -> datetime:
    """把 Mongo document 中的时间值规范为带 timezone 的 datetime。"""

    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value
    raise TypeError(f"expected datetime, got {type(value)!r}")


def _optional_datetime(value: Any) -> datetime | None:
    """解析可空 datetime 字段。"""

    if value is None:
        return None
    return _datetime(value)


def endpoint_to_doc(endpoint: Endpoint | None) -> dict[str, Any] | None:
    """把 Endpoint 转成可嵌入 Mongo document 的结构。"""

    if endpoint is None:
        return None
    return {
        "base_url": endpoint.base_url,
        "token": endpoint.token,
    }


def endpoint_from_doc(doc: dict[str, Any] | None) -> Endpoint | None:
    """从 Mongo document 嵌套结构还原 Endpoint。"""

    if doc is None:
        return None
    return Endpoint(
        base_url=doc["base_url"],
        token=doc.get("token"),
    )


def sandbox_ref_to_doc(ref: SandboxRef) -> dict[str, Any]:
    """把 SandboxRef 转成 sandbox collection 的公共字段。"""

    return {
        "sandbox_id": ref.sandbox_id,
        "provider_id": ref.provider_id,
        "endpoint": endpoint_to_doc(ref.endpoint),
        "metadata": dict(ref.metadata),
    }


def sandbox_ref_from_doc(doc: dict[str, Any]) -> SandboxRef:
    """从 sandbox document 还原 SandboxRef。"""

    return SandboxRef(
        sandbox_id=doc["sandbox_id"],
        provider_id=doc["provider_id"],
        endpoint=endpoint_from_doc(doc.get("endpoint")),
        metadata=dict(doc.get("metadata") or {}),
    )


def sandbox_record_to_doc(record: SandboxRecord) -> dict[str, Any]:
    """把 SandboxRecord 序列化为 Mongo sandbox document。"""

    ref_doc = sandbox_ref_to_doc(record.ref)
    return {
        "_id": record.ref.sandbox_id,
        **ref_doc,
        "state": record.state.value,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
        "owner_user_id": record.owner_user_id,
        "user_binding_id": record.user_binding_id,
        "state_version": record.state_version,
        "last_error": record.last_error,
        "reuse_count": record.reuse_count,
    }


def sandbox_record_from_doc(doc: dict[str, Any]) -> SandboxRecord:
    """从 Mongo sandbox document 还原 SandboxRecord。"""

    return SandboxRecord(
        ref=sandbox_ref_from_doc(doc),
        state=SandboxState(doc["state"]),
        created_at=_datetime(doc["created_at"]),
        updated_at=_datetime(doc["updated_at"]),
        owner_user_id=doc.get("owner_user_id"),
        user_binding_id=doc.get("user_binding_id"),
        state_version=int(doc.get("state_version") or 0),
        last_error=doc.get("last_error"),
        reuse_count=int(doc.get("reuse_count") or 0),
    )


def user_binding_to_doc(binding: UserSandboxBindingRecord) -> dict[str, Any]:
    """把用户绑定记录序列化为 Mongo binding document。"""

    return {
        "_id": binding.user_id,
        "user_binding_id": binding.user_binding_id,
        "sandbox_id": binding.sandbox_id,
        "user_id": binding.user_id,
        "created_at": binding.created_at,
        "updated_at": binding.updated_at,
        "last_active_at": binding.last_active_at,
        "reuse_count": binding.reuse_count,
    }


def user_binding_from_doc(doc: dict[str, Any]) -> UserSandboxBindingRecord:
    """从 Mongo binding document 还原用户绑定记录。"""

    return UserSandboxBindingRecord(
        user_binding_id=doc["user_binding_id"],
        sandbox_id=doc["sandbox_id"],
        user_id=doc["user_id"],
        created_at=_datetime(doc["created_at"]),
        updated_at=_datetime(doc["updated_at"]),
        last_active_at=_datetime(doc["last_active_at"]),
        reuse_count=int(doc.get("reuse_count") or 0),
    )


def workspace_snapshot_to_doc(
    snapshot: WorkspaceSnapshotRef | None,
) -> dict[str, Any] | None:
    """把 WorkspaceSnapshotRef 转成 tombstone_snapshot 嵌套 document。"""

    if snapshot is None:
        return None
    return {
        "workspace_key": snapshot.workspace_key,
        "snapshot_id": snapshot.snapshot_id,
        "created_at": snapshot.created_at,
        "last_accessed_at": snapshot.last_accessed_at,
        "total_bytes": snapshot.total_bytes,
        "file_count": snapshot.file_count,
        "directory_count": snapshot.directory_count,
        "recoverable": snapshot.recoverable,
        "unrecoverable_reason": snapshot.unrecoverable_reason,
        "unrecoverable_at": snapshot.unrecoverable_at,
    }


def workspace_snapshot_from_doc(
    doc: dict[str, Any] | None,
) -> WorkspaceSnapshotRef | None:
    """从 tombstone_snapshot 嵌套 document 还原 WorkspaceSnapshotRef。"""

    if doc is None:
        return None
    return WorkspaceSnapshotRef(
        workspace_key=doc["workspace_key"],
        snapshot_id=doc["snapshot_id"],
        created_at=_datetime(doc["created_at"]),
        last_accessed_at=_datetime(doc["last_accessed_at"]),
        total_bytes=int(doc.get("total_bytes") or 0),
        file_count=int(doc.get("file_count") or 0),
        directory_count=int(doc.get("directory_count") or 0),
        recoverable=bool(doc.get("recoverable", True)),
        unrecoverable_reason=doc.get("unrecoverable_reason"),
        unrecoverable_at=_optional_datetime(doc.get("unrecoverable_at")),
    )


def workspace_record_to_doc(record: WorkspaceRecord) -> dict[str, Any]:
    """把 WorkspaceRecord 序列化为 Mongo workspace document。"""

    return {
        "_id": record.workspace_key,
        "user_id": record.user_id,
        "session_id": record.session_id,
        "workspace_key": record.workspace_key,
        "state": record.state.value,
        "workspace_path": record.workspace_path,
        "tombstone_snapshot": workspace_snapshot_to_doc(record.tombstone_snapshot),
        "generation": record.generation,
        "state_version": record.state_version,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
        "last_accessed_at": record.last_accessed_at,
        "deleted_at": record.deleted_at,
        "restore_started_at": record.restore_started_at,
        "restored_at": record.restored_at,
        "last_error": record.last_error,
    }


def workspace_record_from_doc(doc: dict[str, Any]) -> WorkspaceRecord:
    """从 Mongo workspace document 还原 WorkspaceRecord。"""

    return WorkspaceRecord(
        user_id=doc["user_id"],
        session_id=doc["session_id"],
        workspace_key=doc["workspace_key"],
        state=WorkspaceState(doc["state"]),
        workspace_path=doc.get("workspace_path"),
        tombstone_snapshot=workspace_snapshot_from_doc(doc.get("tombstone_snapshot")),
        generation=int(doc.get("generation") or 0),
        state_version=int(doc.get("state_version") or 0),
        created_at=_datetime(doc["created_at"]),
        updated_at=_datetime(doc["updated_at"]),
        last_accessed_at=_datetime(doc["last_accessed_at"]),
        deleted_at=_optional_datetime(doc.get("deleted_at")),
        restore_started_at=_optional_datetime(doc.get("restore_started_at")),
        restored_at=_optional_datetime(doc.get("restored_at")),
        last_error=doc.get("last_error"),
    )
