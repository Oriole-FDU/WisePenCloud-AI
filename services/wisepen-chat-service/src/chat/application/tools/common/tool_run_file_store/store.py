from __future__ import annotations

import asyncio
import hashlib
import re
import shutil
import tempfile
import time
import uuid
from contextlib import suppress
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from typing import Protocol

from .errors import (
    InvalidToolFileRefError,
    ToolFileNotFoundError,
    ToolFileUnreadableError,
    ToolFileWriteError,
)
from .models import (
    ResolvedToolFile,
    ToolFileRefRecord,
    ToolRunFileCleanupResult,
)
from .utils import sanitize_tool_file_name

DEFAULT_TOOL_RUN_FILE_ROOT = Path(tempfile.gettempdir()) / "wisepen-tool-run-files"
DEFAULT_TOOL_RUN_FILE_REF_TTL_SECONDS = 6 * 60 * 60
DEFAULT_TOOL_RUN_FILE_CLEANUP_GRACE_SECONDS = 10 * 60
DEFAULT_TOOL_RUN_FILE_MAX_BYTES = 50 * 1024 * 1024

_REF_ID_PATTERN = re.compile(r"^tfile_(?:[a-z][a-z0-9]*_)?[A-Za-z0-9]{8,64}$")
_REF_PREFIX_PATTERN = re.compile(r"^[a-z][a-z0-9]*$")
_SAFE_COMPONENT_PATTERN = re.compile(r"[^A-Za-z0-9._-]+")
_HASH_CHUNK_BYTES = 1024 * 1024


class ToolRunFileRepository(Protocol):
    """短期工具文件引用的元数据仓储协议。"""

    async def put(self, record: ToolFileRefRecord, *, ttl_seconds: int) -> None:
        """写入引用记录。

        Args:
            record: 待持久化的文件引用记录。
            ttl_seconds: Redis key 过期时间，单位秒。
        """
        ...

    async def get(self, ref_id: str) -> ToolFileRefRecord | None:
        """按 tfile_* 引用读取记录，不存在时返回 None。"""
        ...

    async def delete(self, ref_id: str) -> None:
        """删除指定 tfile_* 引用记录。"""
        ...


class ToolRunFileStore:
    """工具产出文件的短生命周期移交存储。

    该门面只管理工具产出的临时可消费文件，不接管上传、附件、资产或知识库归属。
    Redis 只保存 tfile_* 元数据，文件字节保存在本地或共享文件系统根目录。
    """

    __slots__ = (
        "_cleanup_grace_seconds",
        "_max_file_size_bytes",
        "_ref_ttl_seconds",
        "_repository",
        "_root_dir",
    )

    def __init__(
        self,
        *,
        repository: ToolRunFileRepository,
        root_dir: str | Path = DEFAULT_TOOL_RUN_FILE_ROOT,
        ref_ttl_seconds: int = DEFAULT_TOOL_RUN_FILE_REF_TTL_SECONDS,
        cleanup_grace_seconds: int = DEFAULT_TOOL_RUN_FILE_CLEANUP_GRACE_SECONDS,
        max_file_size_bytes: int | None = DEFAULT_TOOL_RUN_FILE_MAX_BYTES,
    ) -> None:
        self._repository = repository
        self._root_dir = Path(root_dir)
        self._ref_ttl_seconds = int(ref_ttl_seconds)
        self._cleanup_grace_seconds = int(cleanup_grace_seconds)
        self._max_file_size_bytes = max_file_size_bytes

    def create_staging_dir(
        self,
        *,
        user_id: str,
        session_id: str,
        producer: str,
        run_id: str | None = None,
    ) -> Path:
        """为单次工具运行创建隔离的暂存目录。

        Args:
            user_id: 用户隔离键。
            session_id: 会话隔离键。
            producer: 工具或内部组件名称。
            run_id: 可选运行标识；未传入时自动生成短 ID。

        Returns:
            已创建且位于 store 根目录内的暂存目录路径。

        Raises:
            InvalidToolFileRefError: 解析后的暂存目录逃逸 store 根目录。
            ToolFileWriteError: 根目录创建失败或不是目录。
        """
        root = self._ensure_root()
        safe_user = _safe_component(user_id, fallback="user")
        safe_session = _safe_component(session_id, fallback="session")
        safe_producer = _safe_component(producer, fallback="tool")
        safe_run = _safe_component(run_id or uuid.uuid4().hex[:16], fallback="run")

        staging_dir = (
            root / safe_user / safe_session / "staging" / safe_producer / safe_run
        ).resolve(strict=False)
        _ensure_within_root(staging_dir, root)
        staging_dir.mkdir(parents=True, exist_ok=True)
        return staging_dir

    async def publish_file(
        self,
        *,
        user_id: str,
        session_id: str,
        producer: str,
        path: str | Path,
        filename: str | None = None,
        content_type: str | None = None,
        ttl_seconds: int | None = None,
        metadata: dict[str, object] | None = None,
        ref_prefix: str | None = None,
    ) -> ToolFileRefRecord:
        """发布工具产出的本地文件，返回不透明的 tfile_* 引用。

        Args:
            user_id: 用户隔离键。
            session_id: 会话隔离键。
            producer: 工具或内部组件名称。
            path: 待发布的本地文件路径。
            filename: 可选展示文件名；缺省时使用源文件名。
            content_type: 上游已知 MIME；未知时为空。
            ttl_seconds: 可选引用 TTL；缺省时使用 store 默认 TTL。
            metadata: 调用方附加的轻量元数据。
            ref_prefix: 可选引用前缀（如 "web"），生成 `tfile_{prefix}_{random}`；缺省时为 `tfile_{random}`。

        Returns:
            已写入 Redis 的短期文件引用记录。

        Raises:
            ToolFileUnreadableError: 源文件不存在、不可读、不是普通文件或超过大小限制。
            ToolFileWriteError: TTL 非法、根目录异常或 object 写入失败。
            InvalidToolFileRefError: object 路径逃逸 store 根目录，或 ref_prefix 格式非法。
        """
        source_path, size_bytes = await asyncio.to_thread(
            self._resolve_publish_source, Path(path),
        )
        safe_filename = sanitize_tool_file_name(filename or source_path.name)
        safe_suffix = Path(safe_filename).suffix.lower()
        sha256 = await asyncio.to_thread(_sha256_file, source_path)

        root = self._ensure_root()
        # 内容寻址路径：sha256 前两字节作为一级分桶目录（仿 Git objects 布局）
        object_path = (
            root
            / _safe_component(user_id, fallback="user")
            / _safe_component(session_id, fallback="session")
            / "objects"
            / sha256[:2]
            / f"{sha256}{safe_suffix}"
        ).resolve(strict=False)
        _ensure_within_root(object_path, root)

        await asyncio.to_thread(_copy_to_object_path, source_path, object_path)

        ttl = int(ttl_seconds or self._ref_ttl_seconds)
        if ttl <= 0:
            raise ToolFileWriteError("ttl_seconds must be positive.")

        now = datetime.now(timezone.utc)
        ref_id = _build_ref_id(ref_prefix)
        record = ToolFileRefRecord(
            ref_id=ref_id,
            user_id=user_id,
            session_id=session_id,
            producer=producer,
            sha256=sha256,
            object_rel_path=object_path.relative_to(root).as_posix(),
            filename=safe_filename,
            content_type=content_type,
            size_bytes=size_bytes,
            created_at=now,
            expires_at=now + timedelta(seconds=ttl),
            metadata=dict(metadata or {}),
        )
        await self._repository.put(record, ttl_seconds=ttl)
        return record

    async def publish_bytes(
        self,
        *,
        user_id: str,
        session_id: str,
        producer: str,
        filename: str,
        content: bytes,
        content_type: str | None = None,
        ttl_seconds: int | None = None,
        metadata: dict[str, object] | None = None,
        ref_prefix: str | None = None,
    ) -> ToolFileRefRecord:
        """发布工具已经持有的 bytes 内容，返回不透明的 tfile_* 引用。

        该方法是通用中转入口，不判断文件业务类型，也不负责上传、资产或知识库归属。

        Args:
            user_id: 用户隔离键。
            session_id: 会话隔离键。
            producer: 工具或内部组件名称。
            filename: 展示文件名，会在写入前清洗。
            content: 待发布的文件字节。
            content_type: 上游已知 MIME；未知时为空。
            ttl_seconds: 可选引用 TTL；缺省时使用 store 默认 TTL。
            metadata: 调用方附加的轻量元数据。
            ref_prefix: 可选引用前缀（如 "web"），生成 `tfile_{prefix}_{random}`；缺省时为 `tfile_{random}`。

        Returns:
            已写入 Redis 的短期文件引用记录。

        Raises:
            ToolFileUnreadableError: 内容超过大小限制。
            ToolFileWriteError: staging 写入、TTL 校验或 object 写入失败。
            InvalidToolFileRefError: staging 或 object 路径逃逸 store 根目录，或 ref_prefix 格式非法。
        """
        if self._max_file_size_bytes is not None and len(content) > self._max_file_size_bytes:
            raise ToolFileUnreadableError()

        safe_filename = sanitize_tool_file_name(filename)
        staging_dir = self.create_staging_dir(
            user_id=user_id,
            session_id=session_id,
            producer=producer,
        )
        staging_path = (staging_dir / safe_filename).resolve(strict=False)
        _ensure_within_root(staging_path, self._ensure_root())

        await asyncio.to_thread(_write_bytes_to_file, staging_path, content)
        record = await self.publish_file(
            user_id=user_id,
            session_id=session_id,
            producer=producer,
            path=staging_path,
            filename=safe_filename,
            content_type=content_type,
            ttl_seconds=ttl_seconds,
            metadata=metadata,
            ref_prefix=ref_prefix,
        )
        self.remove_staging_dir(staging_dir)
        return record

    async def resolve_ref(
        self,
        *,
        user_id: str,
        session_id: str,
        ref_id: str,
    ) -> ResolvedToolFile:
        """将 tfile_* 引用解析为已验证的本地文件路径。

        Args:
            user_id: 当前用户隔离键。
            session_id: 当前会话隔离键。
            ref_id: 待解析的 tfile_* 文件引用 ID。

        Returns:
            已校验作用域、TTL、路径和大小的本地文件描述。

        Raises:
            InvalidToolFileRefError: 引用格式非法、作用域不匹配或路径逃逸根目录。
            ToolFileNotFoundError: 引用不存在、已过期或文件不存在。
            ToolFileUnreadableError: 文件不是普通文件或大小校验失败。
        """
        if _REF_ID_PATTERN.fullmatch(ref_id) is None:
            raise InvalidToolFileRefError()

        record = await self._repository.get(ref_id)
        if record is None:
            raise ToolFileNotFoundError()

        # 会话隔离：ref 只能被创建它的用户和会话访问
        if record.user_id != user_id or record.session_id != session_id:
            raise InvalidToolFileRefError()

        if record.expires_at <= datetime.now(timezone.utc):
            await self._repository.delete(ref_id)  # 惰性清理过期记录
            raise ToolFileNotFoundError()

        root = self._ensure_root()
        try:
            object_path = (root / record.object_rel_path).resolve(strict=True)
        except OSError as e:
            raise ToolFileNotFoundError() from e

        _ensure_within_root(object_path, root)  # 防御路径穿越

        try:
            if not object_path.is_file():
                raise ToolFileUnreadableError()
            size_bytes = object_path.stat().st_size
        except OSError as e:
            raise ToolFileUnreadableError() from e

        if size_bytes != record.size_bytes:  # 文件被篡改或意外损坏
            raise ToolFileUnreadableError()

        return ResolvedToolFile(
            ref_id=record.ref_id,
            path=object_path,
            filename=record.filename,
            content_type=record.content_type,
            size_bytes=record.size_bytes,
            sha256=record.sha256,
            producer=record.producer,
            expires_at=record.expires_at,
        )

    def remove_staging_dir(self, staging_dir: str | Path) -> None:
        """尽力清理由本 store 创建的暂存目录。

        Args:
            staging_dir: 待清理的暂存目录路径。

        Raises:
            InvalidToolFileRefError: 目录路径逃逸 store 根目录。
            ToolFileWriteError: 传入目录不位于 staging 命名空间。
        """
        root = self._ensure_root()
        candidate = Path(staging_dir).resolve(strict=False)
        _ensure_within_root(candidate, root)
        if "staging" not in candidate.relative_to(root).parts:
            raise ToolFileWriteError("staging_dir is not under a staging namespace.")
        shutil.rmtree(candidate, ignore_errors=True)

    def cleanup_expired_files(self) -> ToolRunFileCleanupResult:
        """按 mtime 清理过期 object 文件和陈旧暂存目录。

        Redis 负责 tfile_* 元数据 TTL；该方法只负责回收文件系统中的残留字节。

        Returns:
            本次清理扫描、删除和失败数量。

        Raises:
            ToolFileWriteError: store 根目录无法创建或不是目录。
        """
        root = self._ensure_root()
        # TTL + 宽限期之前最后修改的文件视为可回收
        cutoff = time.time() - self._ref_ttl_seconds - self._cleanup_grace_seconds

        scanned_objects = removed_objects = failed_objects = 0
        scanned_staging_dirs = removed_staging_dirs = failed_staging_dirs = 0

        for objects_dir in root.glob("*/*/objects"):
            if not objects_dir.is_dir():
                continue
            for item in list(objects_dir.rglob("*")):  # list() 防止迭代中修改目录树
                if not item.is_file():
                    continue
                scanned_objects += 1
                try:
                    resolved = item.resolve(strict=True)
                    _ensure_within_root(resolved, root)
                    if resolved.stat().st_mtime > cutoff:
                        continue
                    resolved.unlink()
                    removed_objects += 1
                except Exception:
                    failed_objects += 1
            _remove_empty_children(objects_dir)

        for staging_root in root.glob("*/*/staging"):
            if not staging_root.is_dir():
                continue
            for run_dir in list(staging_root.glob("*/*")):  # list() 防止迭代中修改目录树
                if not run_dir.is_dir():
                    continue
                scanned_staging_dirs += 1
                try:
                    resolved = run_dir.resolve(strict=True)
                    _ensure_within_root(resolved, root)
                    if resolved.stat().st_mtime > cutoff:
                        continue
                    shutil.rmtree(resolved)
                    removed_staging_dirs += 1
                except Exception:
                    failed_staging_dirs += 1
            _remove_empty_children(staging_root)

        return ToolRunFileCleanupResult(
            scanned_objects=scanned_objects,
            removed_objects=removed_objects,
            failed_objects=failed_objects,
            scanned_staging_dirs=scanned_staging_dirs,
            removed_staging_dirs=removed_staging_dirs,
            failed_staging_dirs=failed_staging_dirs,
        )

    def _resolve_publish_source(self, path: Path) -> tuple[Path, int]:
        """解析并校验发布源文件，返回 resolved_path 和 size_bytes。"""
        try:
            source_path = path.resolve(strict=True)
            if not source_path.is_file():
                raise ToolFileUnreadableError()  # 非 OSError，穿透 except OSError 块
            size_bytes = source_path.stat().st_size
        except OSError as e:
            raise ToolFileUnreadableError() from e

        if self._max_file_size_bytes is not None and size_bytes > self._max_file_size_bytes:
            raise ToolFileUnreadableError()
        return source_path, size_bytes

    def _ensure_root(self) -> Path:
        """确保根目录存在并返回其 resolved 路径。"""
        try:
            root = self._root_dir.resolve(strict=False)
            root.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            raise ToolFileWriteError() from e
        if not root.is_dir():
            raise ToolFileWriteError("tool run file root is not a directory.")
        return root


def _safe_component(value: str | None, *, fallback: str) -> str:
    """将任意字符串清理为安全的单层路径组件，防止路径穿越。"""
    raw = PurePosixPath(str(value or "").replace("\\", "/")).name
    safe = _SAFE_COMPONENT_PATTERN.sub("_", raw).strip("._-")
    return safe or fallback


def _build_ref_id(prefix: str | None) -> str:
    """生成 tfile_* 引用 ID。有前缀时为 `tfile_{prefix}_{random}`，无前缀时为 `tfile_{random}`。"""
    random_part = uuid.uuid4().hex[:16]
    if prefix:
        if _REF_PREFIX_PATTERN.fullmatch(prefix) is None:
            raise InvalidToolFileRefError()
        return f"tfile_{prefix}_{random_part}"
    return f"tfile_{random_part}"


def _sha256_file(path: Path) -> str:
    """分块计算文件 SHA-256，支持大文件。"""
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(_HASH_CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


def _copy_to_object_path(source_path: Path, object_path: Path) -> None:
    """将文件内容寻址写入 object 路径；已存在则仅 touch 刷新 mtime（相同内容去重）。"""
    object_path.parent.mkdir(parents=True, exist_ok=True)
    if object_path.exists():
        object_path.touch()  # 内容相同的文件只存一份，touch 延长其清理宽限期
        return

    # 先写临时文件再 rename，保证目标路径写入的原子性
    tmp_path = object_path.with_name(f".{object_path.name}.{uuid.uuid4().hex}.tmp")
    try:
        shutil.copyfile(source_path, tmp_path)
        tmp_path.replace(object_path)
    except OSError as e:
        raise ToolFileWriteError() from e
    finally:
        with suppress(OSError):
            tmp_path.unlink(missing_ok=True)


def _write_bytes_to_file(path: Path, content: bytes) -> None:
    """将 bytes 原子写入目标文件，避免消费者看到半写入内容。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        tmp_path.write_bytes(content)
        tmp_path.replace(path)
    except OSError as e:
        raise ToolFileWriteError() from e
    finally:
        with suppress(OSError):
            tmp_path.unlink(missing_ok=True)


def _ensure_within_root(path: Path, root: Path) -> None:
    """断言 path 位于 root 之下，否则抛 InvalidToolFileRefError（防路径穿越）。"""
    try:
        path.resolve(strict=False).relative_to(root.resolve(strict=False))
    except (ValueError, OSError) as e:
        raise InvalidToolFileRefError() from e


def _remove_empty_children(root: Path) -> None:
    """从最深层开始向上删除空目录（倒序保证子目录先于父目录被尝试删除）。"""
    for item in sorted(root.rglob("*"), key=lambda p: len(p.parts), reverse=True):
        if item.is_dir():
            with suppress(OSError):
                item.rmdir()  # 非空目录会抛 OSError，suppress 静默跳过


