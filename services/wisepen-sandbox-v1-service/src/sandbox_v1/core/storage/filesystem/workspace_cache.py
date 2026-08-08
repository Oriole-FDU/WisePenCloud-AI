from __future__ import annotations

import asyncio
import json
import os
import shutil
import stat
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path, PurePosixPath

from common.core.exceptions import ServiceException

from sandbox_v1.domain.entities import (
    WorkspaceEvictionReason,
    WorkspaceRestoreOutcome,
    WorkspaceSnapshotRef,
    utc_now,
)
from sandbox_v1.domain.error_codes import SandboxErrorCode


_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class _FsEntry:
    """快照 metadata 中记录的一个目录或文件条目。"""

    relative_path: str
    mode: int
    mtime_ns: int


@dataclass(frozen=True)
class _SnapshotMetadata:
    """一个 Workspace 快照 generation 的完整 metadata。"""

    ref: WorkspaceSnapshotRef
    user_id: str
    session_id: str
    directories: list[_FsEntry]
    files: list[_FsEntry]


class LocalWorkspaceSnapshotCache:
    """基于本地文件系统的 Workspace 快照缓存。

    快照 generation 一旦发布就不可变。rebuild 使用 tombstone 上记录的精确
    snapshot_id，而不是 latest/current 指针；即使后续回收阶段写入了更新快照，
    逻辑删除后的显式 rebuild 仍然是确定性的。
    """

    def __init__(
        self,
        *,
        cache_root: str | Path,
        ttl_seconds: int = 7 * 24 * 60 * 60,
        max_bytes: int = 0,
        high_watermark_ratio: float = 0.8,
        target_watermark_ratio: float = 0.7,
    ) -> None:
        self._cache_root = Path(cache_root)
        self._snapshot_root = self._cache_root / "snapshots"
        self._tmp_root = self._cache_root / ".tmp"
        self._ttl = timedelta(seconds=max(1, ttl_seconds))
        self._max_bytes = max(0, max_bytes)
        self._high_ratio = max(0.01, min(1.0, high_watermark_ratio))
        self._target_ratio = max(0.0, min(self._high_ratio, target_watermark_ratio))

    async def snapshot(
        self,
        *,
        workspace_key: str,
        user_id: str,
        session_id: str,
        source_path: Path,
    ) -> WorkspaceSnapshotRef | None:
        """在线程池中为一个 Workspace 创建快照。"""

        return await asyncio.to_thread(
            self._snapshot_sync,
            workspace_key,
            user_id,
            session_id,
            source_path,
        )

    async def restore(
        self,
        snapshot: WorkspaceSnapshotRef | None,
        *,
        target_path: Path,
    ) -> WorkspaceRestoreOutcome:
        """在线程池中把指定快照恢复到目标目录。"""

        return await asyncio.to_thread(self._restore_sync, snapshot, target_path)

    async def evict_expired(self) -> list[WorkspaceSnapshotRef]:
        """在线程池中淘汰超过 TTL 的可恢复快照。"""

        return await asyncio.to_thread(self._evict_expired_sync)

    async def evict_lru(self) -> list[WorkspaceSnapshotRef]:
        """在线程池中按 LRU 淘汰超出容量水位的可恢复快照。"""

        return await asyncio.to_thread(self._evict_lru_sync)

    async def mark_unrecoverable(
        self,
        snapshot: WorkspaceSnapshotRef,
        reason: WorkspaceEvictionReason,
    ) -> WorkspaceSnapshotRef:
        """在线程池中把快照标记为不可恢复。"""

        return await asyncio.to_thread(
            self._mark_unrecoverable_sync,
            snapshot,
            reason,
        )

    def _snapshot_sync(
        self,
        workspace_key: str,
        user_id: str,
        session_id: str,
        source_path: Path,
    ) -> WorkspaceSnapshotRef | None:
        """同步创建快照，并用临时目录到最终目录的 rename 发布。"""

        # workspace_key/snapshot_id 只能作为路径组件，不能包含分隔符或相对路径。
        self._validate_component(workspace_key, "workspace_key")
        if not source_path.exists():
            return None

        # Workspace 根必须是真实目录，避免跟随 symlink 逃逸受管边界。
        if source_path.is_symlink() or not source_path.is_dir():
            raise ServiceException(
                SandboxErrorCode.WORKSPACE_SNAPSHOT_REJECTED,
                "workspace root must be a real directory",
            )

        snapshot_id = f"{utc_now().strftime('%Y%m%d%H%M%S%f')}_{uuid.uuid4().hex}"
        key_root = self._snapshot_root / workspace_key
        final_dir = key_root / snapshot_id
        tmp_dir = self._tmp_root / f"{workspace_key}_{snapshot_id}"
        data_dir = tmp_dir / "files"

        # 同名临时目录残留时先清理，保证本次快照内容独立。
        if tmp_dir.exists():
            shutil.rmtree(tmp_dir)
        data_dir.mkdir(parents=True, exist_ok=False)

        try:
            # 先复制文件树并收集权限/mtime metadata。
            directories, files, total_bytes = self._copy_workspace_tree(
                source_path,
                data_dir,
            )
            ref = WorkspaceSnapshotRef(
                workspace_key=workspace_key,
                snapshot_id=snapshot_id,
                total_bytes=total_bytes,
                file_count=len(files),
                directory_count=len(directories),
            )
            metadata = _SnapshotMetadata(
                ref=ref,
                user_id=user_id,
                session_id=session_id,
                directories=directories,
                files=files,
            )
            # metadata 与 files 一起先写入临时目录，避免发布半成品快照。
            self._write_metadata(tmp_dir / "metadata.json", metadata)

            # replace 到最终目录作为发布点；current 指针仅用于人工/后续扩展查看。
            key_root.mkdir(parents=True, exist_ok=True)
            tmp_dir.replace(final_dir)
            self._publish_current_pointer(key_root, snapshot_id)
            return ref
        except Exception:
            # 任意失败都清理临时目录，避免下次 snapshot 读到残留内容。
            if tmp_dir.exists():
                shutil.rmtree(tmp_dir, ignore_errors=True)
            raise

    def _copy_workspace_tree(
        self,
        source_root: Path,
        target_root: Path,
    ) -> tuple[list[_FsEntry], list[_FsEntry], int]:
        """复制 Workspace 文件树，并记录恢复所需的目录/文件 metadata。"""

        directories: list[_FsEntry] = []
        files: list[_FsEntry] = []
        total_bytes = 0

        # 根目录也要记录权限和 mtime，恢复结束时再统一应用。
        root_stat = source_root.stat(follow_symlinks=False)
        directories.append(self._entry(".", root_stat))

        for current_root, dir_names, file_names in os.walk(source_root):
            current = Path(current_root)

            # 目录只接受真实目录，拒绝 symlink、device、FIFO 等非 Workspace 普通内容。
            for dirname in list(dir_names):
                src = current / dirname
                rel = self._relative_to_root(src, source_root)
                src_stat = src.stat(follow_symlinks=False)
                if stat.S_ISLNK(src_stat.st_mode) or not stat.S_ISDIR(src_stat.st_mode):
                    # The cache never follows symlinks or serializes device/FIFO
                    # nodes. Restoring those would escape the Workspace contract.
                    raise ServiceException(
                        SandboxErrorCode.WORKSPACE_SNAPSHOT_REJECTED,
                        f"unsupported workspace entry: {rel}",
                    )
                dst = self._safe_join(target_root, rel)
                dst.mkdir(parents=True, exist_ok=True)
                directories.append(self._entry(rel, src_stat))

            # 文件只接受普通文件，并保留文件权限、mtime 和内容。
            for filename in file_names:
                src = current / filename
                rel = self._relative_to_root(src, source_root)
                src_stat = src.stat(follow_symlinks=False)
                if stat.S_ISLNK(src_stat.st_mode) or not stat.S_ISREG(src_stat.st_mode):
                    raise ServiceException(
                        SandboxErrorCode.WORKSPACE_SNAPSHOT_REJECTED,
                        f"unsupported workspace entry: {rel}",
                    )
                dst = self._safe_join(target_root, rel)
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst, follow_symlinks=False)
                os.chmod(dst, stat.S_IMODE(src_stat.st_mode))
                files.append(self._entry(rel, src_stat))
                total_bytes += src_stat.st_size

        # 子目录创建完成后再应用目录 metadata，避免后续写文件改变目录 mtime。
        self._apply_directory_metadata(target_root, directories)
        return directories, files, total_bytes

    def _restore_sync(
        self,
        snapshot: WorkspaceSnapshotRef | None,
        target_path: Path,
    ) -> WorkspaceRestoreOutcome:
        """同步恢复快照；快照不可用时降级为空 Workspace。"""

        # 没有 tombstone 快照时，rebuild 创建空 Workspace 并返回原因。
        if snapshot is None:
            self._replace_with_empty_dir(target_path)
            return WorkspaceRestoreOutcome(
                restored_from_snapshot=False,
                unrecoverable_reason="snapshot_missing",
            )

        # metadata 缺失意味着快照无法恢复，但 rebuild 仍需要给用户一个空目录。
        metadata = self._read_metadata(snapshot.workspace_key, snapshot.snapshot_id)
        if metadata is None:
            self._replace_with_empty_dir(target_path)
            return WorkspaceRestoreOutcome(
                restored_from_snapshot=False,
                snapshot_id=snapshot.snapshot_id,
                unrecoverable_reason="snapshot_missing",
            )

        # 已被 TTL/LRU 标记不可恢复的快照保留原因，但不恢复文件内容。
        if not metadata.ref.recoverable:
            self._replace_with_empty_dir(target_path)
            return WorkspaceRestoreOutcome(
                restored_from_snapshot=False,
                snapshot_id=snapshot.snapshot_id,
                unrecoverable_reason=metadata.ref.unrecoverable_reason
                or "snapshot_unrecoverable",
            )

        source_data = (
            self._snapshot_dir(snapshot.workspace_key, snapshot.snapshot_id) / "files"
        )
        # metadata 存在但 files 已缺失时，也降级为空目录。
        if not source_data.exists():
            self._replace_with_empty_dir(target_path)
            return WorkspaceRestoreOutcome(
                restored_from_snapshot=False,
                snapshot_id=snapshot.snapshot_id,
                unrecoverable_reason="snapshot_missing",
            )

        # 先清空目标目录，再按 metadata 恢复目录、文件和权限时间戳。
        self._replace_with_empty_dir(target_path)
        for directory in metadata.directories:
            self._safe_join(target_path, directory.relative_path).mkdir(
                parents=True,
                exist_ok=True,
            )
        for file_entry in metadata.files:
            src = self._safe_join(source_data, file_entry.relative_path)
            dst = self._safe_join(target_path, file_entry.relative_path)
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst, follow_symlinks=False)
            os.chmod(dst, file_entry.mode)
            os.utime(dst, ns=(file_entry.mtime_ns, file_entry.mtime_ns))

        # 最后恢复目录 metadata，并刷新快照访问时间用于 LRU。
        self._apply_directory_metadata(target_path, metadata.directories)
        self._touch_snapshot(metadata.ref)
        return WorkspaceRestoreOutcome(
            restored_from_snapshot=True,
            snapshot_id=snapshot.snapshot_id,
        )

    def _evict_expired_sync(self) -> list[WorkspaceSnapshotRef]:
        """同步执行 TTL 淘汰，返回被标记不可恢复的快照。"""

        cutoff = utc_now() - self._ttl
        evicted: list[WorkspaceSnapshotRef] = []
        for metadata in self._iter_metadata():
            if not metadata.ref.recoverable:
                continue
            if metadata.ref.last_accessed_at <= cutoff:
                evicted.append(
                    self._mark_unrecoverable_sync(
                        metadata.ref,
                        WorkspaceEvictionReason.TTL,
                    )
                )
        return evicted

    def _evict_lru_sync(self) -> list[WorkspaceSnapshotRef]:
        """同步执行容量水位 LRU 淘汰。"""

        if self._max_bytes <= 0:
            return []

        # 只统计仍可恢复的快照；不可恢复 metadata 不再计入容量。
        metadata = [item for item in self._iter_metadata() if item.ref.recoverable]
        total_bytes = sum(item.ref.total_bytes for item in metadata)
        high_bytes = int(self._max_bytes * self._high_ratio)
        target_bytes = int(self._max_bytes * self._target_ratio)
        if total_bytes <= high_bytes:
            return []

        # 超过高水位时，从最久未访问的快照开始淘汰到目标水位。
        evicted: list[WorkspaceSnapshotRef] = []
        for item in sorted(metadata, key=lambda value: value.ref.last_accessed_at):
            if total_bytes <= target_bytes:
                break
            evicted_ref = self._mark_unrecoverable_sync(
                item.ref,
                WorkspaceEvictionReason.LRU,
            )
            evicted.append(evicted_ref)
            total_bytes -= item.ref.total_bytes
        return evicted

    def _mark_unrecoverable_sync(
        self,
        snapshot: WorkspaceSnapshotRef,
        reason: WorkspaceEvictionReason,
    ) -> WorkspaceSnapshotRef:
        """把快照标记为不可恢复，并删除其文件内容释放空间。"""

        metadata = self._read_metadata(snapshot.workspace_key, snapshot.snapshot_id)
        if metadata is None:
            # metadata 不存在时返回一个不可恢复 ref，供 Repository 回写 tombstone。
            return WorkspaceSnapshotRef(
                workspace_key=snapshot.workspace_key,
                snapshot_id=snapshot.snapshot_id,
                created_at=snapshot.created_at,
                last_accessed_at=snapshot.last_accessed_at,
                total_bytes=snapshot.total_bytes,
                file_count=snapshot.file_count,
                directory_count=snapshot.directory_count,
                recoverable=False,
                unrecoverable_reason=reason.value,
                unrecoverable_at=utc_now(),
            )

        ref = WorkspaceSnapshotRef(
            workspace_key=metadata.ref.workspace_key,
            snapshot_id=metadata.ref.snapshot_id,
            created_at=metadata.ref.created_at,
            last_accessed_at=metadata.ref.last_accessed_at,
            total_bytes=metadata.ref.total_bytes,
            file_count=metadata.ref.file_count,
            directory_count=metadata.ref.directory_count,
            recoverable=False,
            unrecoverable_reason=reason.value,
            unrecoverable_at=utc_now(),
        )
        updated = _SnapshotMetadata(
            ref=ref,
            user_id=metadata.user_id,
            session_id=metadata.session_id,
            directories=metadata.directories,
            files=metadata.files,
        )
        snapshot_dir = self._snapshot_dir(ref.workspace_key, ref.snapshot_id)

        # 淘汰释放文件内容，但故意保留 metadata，作为 rebuild 降级为空目录的原因。
        self._write_metadata(snapshot_dir / "metadata.json", updated)
        shutil.rmtree(snapshot_dir / "files", ignore_errors=True)
        return ref

    def _iter_metadata(self) -> list[_SnapshotMetadata]:
        """遍历缓存根目录下所有可解析 metadata。"""

        if not self._snapshot_root.exists():
            return []

        result: list[_SnapshotMetadata] = []
        for metadata_path in self._snapshot_root.glob("*/*/metadata.json"):
            metadata = self._read_metadata_path(metadata_path)
            if metadata is not None:
                result.append(metadata)
        return result

    def _touch_snapshot(self, snapshot: WorkspaceSnapshotRef) -> None:
        """刷新快照 last_accessed_at，用于后续 LRU 判断。"""

        metadata = self._read_metadata(snapshot.workspace_key, snapshot.snapshot_id)
        if metadata is None:
            return
        touched = WorkspaceSnapshotRef(
            workspace_key=metadata.ref.workspace_key,
            snapshot_id=metadata.ref.snapshot_id,
            created_at=metadata.ref.created_at,
            last_accessed_at=utc_now(),
            total_bytes=metadata.ref.total_bytes,
            file_count=metadata.ref.file_count,
            directory_count=metadata.ref.directory_count,
            recoverable=metadata.ref.recoverable,
            unrecoverable_reason=metadata.ref.unrecoverable_reason,
            unrecoverable_at=metadata.ref.unrecoverable_at,
        )
        self._write_metadata(
            self._snapshot_dir(touched.workspace_key, touched.snapshot_id)
            / "metadata.json",
            _SnapshotMetadata(
                ref=touched,
                user_id=metadata.user_id,
                session_id=metadata.session_id,
                directories=metadata.directories,
                files=metadata.files,
            ),
        )

    def _snapshot_dir(self, workspace_key: str, snapshot_id: str) -> Path:
        """返回某个 snapshot 的目录，并校验路径组件安全。"""

        self._validate_component(workspace_key, "workspace_key")
        self._validate_component(snapshot_id, "snapshot_id")
        return self._snapshot_root / workspace_key / snapshot_id

    def _read_metadata(
        self,
        workspace_key: str,
        snapshot_id: str,
    ) -> _SnapshotMetadata | None:
        """读取指定 snapshot 的 metadata。"""

        return self._read_metadata_path(
            self._snapshot_dir(workspace_key, snapshot_id) / "metadata.json"
        )

    def _read_metadata_path(self, metadata_path: Path) -> _SnapshotMetadata | None:
        """从 metadata.json 解析快照 metadata，schema 不匹配时视为不可用。"""

        if not metadata_path.exists():
            return None
        raw = json.loads(metadata_path.read_text(encoding="utf-8"))
        if raw.get("schema_version") != _SCHEMA_VERSION:
            return None
        ref = WorkspaceSnapshotRef(
            workspace_key=raw["workspace_key"],
            snapshot_id=raw["snapshot_id"],
            created_at=self._parse_datetime(raw["created_at"]),
            last_accessed_at=self._parse_datetime(raw["last_accessed_at"]),
            total_bytes=int(raw.get("total_bytes") or 0),
            file_count=int(raw.get("file_count") or 0),
            directory_count=int(raw.get("directory_count") or 0),
            recoverable=bool(raw.get("recoverable", True)),
            unrecoverable_reason=raw.get("unrecoverable_reason"),
            unrecoverable_at=(
                self._parse_datetime(raw["unrecoverable_at"])
                if raw.get("unrecoverable_at") else None
            ),
        )
        return _SnapshotMetadata(
            ref=ref,
            user_id=raw["user_id"],
            session_id=raw["session_id"],
            directories=[
                _FsEntry(
                    relative_path=item["path"],
                    mode=int(item["mode"]),
                    mtime_ns=int(item["mtime_ns"]),
                )
                for item in raw.get("directories", [])
            ],
            files=[
                _FsEntry(
                    relative_path=item["path"],
                    mode=int(item["mode"]),
                    mtime_ns=int(item["mtime_ns"]),
                )
                for item in raw.get("files", [])
            ],
        )

    def _write_metadata(self, metadata_path: Path, metadata: _SnapshotMetadata) -> None:
        """原子写入 snapshot metadata。"""

        metadata_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": _SCHEMA_VERSION,
            "workspace_key": metadata.ref.workspace_key,
            "snapshot_id": metadata.ref.snapshot_id,
            "user_id": metadata.user_id,
            "session_id": metadata.session_id,
            "created_at": metadata.ref.created_at.isoformat(),
            "last_accessed_at": metadata.ref.last_accessed_at.isoformat(),
            "total_bytes": metadata.ref.total_bytes,
            "file_count": metadata.ref.file_count,
            "directory_count": metadata.ref.directory_count,
            "recoverable": metadata.ref.recoverable,
            "unrecoverable_reason": metadata.ref.unrecoverable_reason,
            "unrecoverable_at": (
                metadata.ref.unrecoverable_at.isoformat()
                if metadata.ref.unrecoverable_at else None
            ),
            "directories": [
                {
                    "path": item.relative_path,
                    "mode": item.mode,
                    "mtime_ns": item.mtime_ns,
                }
                for item in metadata.directories
            ],
            "files": [
                {
                    "path": item.relative_path,
                    "mode": item.mode,
                    "mtime_ns": item.mtime_ns,
                }
                for item in metadata.files
            ],
        }
        tmp_path = metadata_path.with_suffix(".json.tmp")
        tmp_path.write_text(
            json.dumps(payload, ensure_ascii=True, sort_keys=True),
            encoding="utf-8",
        )
        tmp_path.replace(metadata_path)

    def _publish_current_pointer(self, key_root: Path, snapshot_id: str) -> None:
        """发布当前最新快照指针，供人工排查或后续扩展使用。"""

        pointer = key_root / "current.json"
        tmp_pointer = key_root / "current.json.tmp"
        tmp_pointer.write_text(
            json.dumps({"snapshot_id": snapshot_id}, ensure_ascii=True),
            encoding="utf-8",
        )
        tmp_pointer.replace(pointer)

    @staticmethod
    def _entry(relative_path: str, value: os.stat_result) -> _FsEntry:
        """从 stat 结果提取恢复所需的权限和 mtime。"""

        return _FsEntry(
            relative_path=relative_path,
            mode=stat.S_IMODE(value.st_mode),
            mtime_ns=value.st_mtime_ns,
        )

    @staticmethod
    def _relative_to_root(path: Path, root: Path) -> str:
        """把绝对路径转换为快照 metadata 使用的 POSIX 相对路径。"""

        relative = path.relative_to(root).as_posix()
        return relative or "."

    @staticmethod
    def _safe_join(root: Path, relative_path: str) -> Path:
        """安全拼接快照相对路径，拒绝绝对路径和父目录逃逸。"""

        relative = PurePosixPath(relative_path)
        if relative_path == ".":
            return root
        if relative.is_absolute() or ".." in relative.parts:
            raise ServiceException(
                SandboxErrorCode.WORKSPACE_PATH_UNSAFE,
                f"unsafe snapshot path: {relative_path}",
            )
        return root.joinpath(*relative.parts)

    @staticmethod
    def _apply_directory_metadata(root: Path, directories: list[_FsEntry]) -> None:
        """按从深到浅的顺序恢复目录权限和 mtime。"""

        for directory in sorted(
            directories,
            key=lambda item: item.relative_path.count("/"),
            reverse=True,
        ):
            path = LocalWorkspaceSnapshotCache._safe_join(
                root,
                directory.relative_path,
            )
            os.chmod(path, directory.mode)
            os.utime(path, ns=(directory.mtime_ns, directory.mtime_ns))

    @staticmethod
    def _replace_with_empty_dir(path: Path) -> None:
        """把目标路径替换成空目录。"""

        if path.exists() or path.is_symlink():
            if path.is_dir() and not path.is_symlink():
                shutil.rmtree(path)
            else:
                path.unlink()
        path.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _parse_datetime(value: str) -> datetime:
        """解析 metadata 中的 ISO datetime 字符串。"""

        return datetime.fromisoformat(value)

    @staticmethod
    def _validate_component(value: str, label: str) -> None:
        """校验 workspace_key/snapshot_id 只能作为单个安全路径组件。"""

        allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-")
        if not value or any(char not in allowed for char in value):
            raise ServiceException(
                SandboxErrorCode.WORKSPACE_PATH_UNSAFE,
                f"unsafe {label}: {value}",
            )
