from __future__ import annotations

import json
import os
import stat
from datetime import timedelta
from pathlib import Path

import pytest
from common.core.exceptions import ServiceException

from sandbox_v1.core.storage.filesystem import LocalWorkspaceSnapshotCache
from sandbox_v1.domain.entities import utc_now
from sandbox_v1.domain.error_codes import SandboxErrorCode


def _set_metadata_time(cache_root: Path, snapshot_id: str, timestamp: str) -> None:
    metadata_path = next(cache_root.glob(f"snapshots/*/{snapshot_id}/metadata.json"))
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["last_accessed_at"] = timestamp
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")


@pytest.mark.asyncio
async def test_snapshot_restore_preserves_regular_files_metadata(tmp_path: Path) -> None:
    source = tmp_path / "workspace"
    nested = source / "nested"
    nested.mkdir(parents=True)
    file_path = nested / "note.txt"
    file_path.write_text("hello", encoding="utf-8")

    file_mtime_ns = 1_700_000_000_123_456_789
    dir_mtime_ns = 1_700_000_100_000_000_000
    os.chmod(file_path, 0o600)
    os.utime(file_path, ns=(file_mtime_ns, file_mtime_ns))
    os.utime(nested, ns=(dir_mtime_ns, dir_mtime_ns))
    expected_file_mtime_ns = file_path.stat().st_mtime_ns
    expected_dir_mtime_ns = nested.stat().st_mtime_ns

    cache = LocalWorkspaceSnapshotCache(cache_root=tmp_path / "cache")
    snapshot = await cache.snapshot(
        workspace_key="workspace-a",
        user_id="user-a",
        session_id="session-a",
        source_path=source,
    )

    assert snapshot is not None
    target = tmp_path / "restored"
    outcome = await cache.restore(snapshot, target_path=target)

    restored_file = target / "nested" / "note.txt"
    assert outcome.restored_from_snapshot is True
    assert restored_file.read_text(encoding="utf-8") == "hello"
    assert stat.S_IMODE(restored_file.stat().st_mode) == stat.S_IMODE(
        file_path.stat().st_mode
    )
    assert restored_file.stat().st_mtime_ns == expected_file_mtime_ns
    assert (target / "nested").stat().st_mtime_ns == expected_dir_mtime_ns


@pytest.mark.asyncio
async def test_snapshot_rejects_symlink_entries(tmp_path: Path) -> None:
    source = tmp_path / "workspace"
    source.mkdir()
    (source / "real.txt").write_text("real", encoding="utf-8")
    try:
        os.symlink(source / "real.txt", source / "link.txt")
    except (OSError, NotImplementedError):
        return

    cache = LocalWorkspaceSnapshotCache(cache_root=tmp_path / "cache")
    with pytest.raises(ServiceException) as exc:
        await cache.snapshot(
            workspace_key="workspace-a",
            user_id="user-a",
            session_id="session-a",
            source_path=source,
        )

    assert exc.value.code == SandboxErrorCode.WORKSPACE_SNAPSHOT_REJECTED.code


@pytest.mark.asyncio
async def test_ttl_eviction_writes_unrecoverable_marker(tmp_path: Path) -> None:
    source = tmp_path / "workspace"
    source.mkdir()
    (source / "old.txt").write_text("old", encoding="utf-8")
    cache_root = tmp_path / "cache"
    cache = LocalWorkspaceSnapshotCache(cache_root=cache_root, ttl_seconds=1)

    snapshot = await cache.snapshot(
        workspace_key="workspace-a",
        user_id="user-a",
        session_id="session-a",
        source_path=source,
    )
    assert snapshot is not None
    old_time = (utc_now() - timedelta(days=8)).isoformat()
    _set_metadata_time(cache_root, snapshot.snapshot_id, old_time)

    evicted = await cache.evict_expired()
    assert [item.snapshot_id for item in evicted] == [snapshot.snapshot_id]

    metadata_path = next(cache_root.glob(f"snapshots/*/{snapshot.snapshot_id}/metadata.json"))
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    assert metadata["recoverable"] is False
    assert metadata["unrecoverable_reason"] == "ttl"
    assert not (metadata_path.parent / "files").exists()

    restored = await cache.restore(snapshot, target_path=tmp_path / "restored")
    assert restored.restored_from_snapshot is False
    assert restored.unrecoverable_reason == "ttl"


@pytest.mark.asyncio
async def test_lru_eviction_marks_oldest_snapshot_unrecoverable(tmp_path: Path) -> None:
    cache_root = tmp_path / "cache"
    cache = LocalWorkspaceSnapshotCache(
        cache_root=cache_root,
        max_bytes=20,
        high_watermark_ratio=0.8,
        target_watermark_ratio=0.7,
    )

    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    (first / "payload.txt").write_text("a" * 10, encoding="utf-8")
    (second / "payload.txt").write_text("b" * 10, encoding="utf-8")

    first_snapshot = await cache.snapshot(
        workspace_key="workspace-a",
        user_id="user-a",
        session_id="session-a",
        source_path=first,
    )
    second_snapshot = await cache.snapshot(
        workspace_key="workspace-b",
        user_id="user-b",
        session_id="session-b",
        source_path=second,
    )
    assert first_snapshot is not None
    assert second_snapshot is not None

    old_time = (utc_now() - timedelta(days=1)).isoformat()
    _set_metadata_time(cache_root, first_snapshot.snapshot_id, old_time)

    evicted = await cache.evict_lru()
    assert [item.snapshot_id for item in evicted] == [first_snapshot.snapshot_id]
    assert evicted[0].unrecoverable_reason == "lru"
