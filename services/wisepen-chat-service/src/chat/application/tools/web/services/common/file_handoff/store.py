from __future__ import annotations

import re
import shutil
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Optional

from chat.application.infra.document_temp_files.path import (
    sanitize_document_filename,
    session_root_for,
)
from common.logger import log_event
from .errors import FileHandoffInvalidSuffixError, FileHandoffWriteError

DEFAULT_HANDOFF_ROOT = Path(tempfile.gettempdir()) / "wisepen-chat-upload-files"

_ALLOWED_HANDOFF_SUFFIXES = frozenset(
    {
        ".pdf",
        ".md",
        ".markdown",
        ".txt",
        ".docx",
        ".docm",
        ".pptx",
        ".pptm",
        ".epub",
        ".xlsx",
        ".xls",
        ".xlsm",
        ".ods",
    }
)
_DANGEROUS_INNER_SUFFIXES = frozenset(
    {
        ".bat",
        ".cmd",
        ".com",
        ".dll",
        ".exe",
        ".jar",
        ".js",
        ".msi",
        ".ps1",
        ".scr",
        ".sh",
        ".vbs",
    }
)



@dataclass(frozen=True, slots=True)
class FileHandoffResult:
    
    file_ref: str
    local_path: Path
    filename: str
    size_bytes: int
    user_id: str
    session_id: str
    original_file_name: str
    content_type: Optional[str] = None



class TemporaryFileHandoffStore:
    
    def __init__(
        self,
        *,
        root_dir: Path,
    ):
        """初始化对象依赖。"""
        self._root_dir = root_dir

    def write_bytes(
        self,
        *,
        user_id: str,
        session_id: str,
        filename: str,
        content: bytes,
        canonical_suffix: str,
        content_type: Optional[str] = None,
    ) -> FileHandoffResult:
        """写入当前流程。"""
        suffix = _validate_suffix(canonical_suffix)
        target = self._build_target_path(
            user_id=user_id,
            session_id=session_id,
            filename=filename,
            canonical_suffix=suffix,
        )

        try:
            target.write_bytes(content)
        except OSError as e:
            raise FileHandoffWriteError() from e

        return FileHandoffResult(
            file_ref=str(target.resolve(strict=False)),
            local_path=target,
            filename=target.name,
            size_bytes=len(content),
            user_id=user_id,
            session_id=session_id,
            original_file_name=filename,
            content_type=content_type,
        )

    def copy_file(
        self,
        *,
        user_id: str,
        session_id: str,
        source_path: Path,
        filename: str,
        canonical_suffix: str,
        content_type: Optional[str] = None,
    ) -> FileHandoffResult:
        """复制当前流程。"""
        suffix = _validate_suffix(canonical_suffix)
        target = self._build_target_path(
            user_id=user_id,
            session_id=session_id,
            filename=filename,
            canonical_suffix=suffix,
        )

        try:
            shutil.copyfile(source_path, target)
            size_bytes = target.stat().st_size
        except OSError as e:
            raise FileHandoffWriteError() from e

        return FileHandoffResult(
            file_ref=str(target.resolve(strict=False)),
            local_path=target,
            filename=target.name,
            size_bytes=size_bytes,
            user_id=user_id,
            session_id=session_id,
            original_file_name=filename,
            content_type=content_type,
        )

    def _build_target_path(
        self,
        *,
        user_id: str,
        session_id: str,
        filename: str,
        canonical_suffix: str,
    ) -> Path:
        """构建当前流程。"""
        if not isinstance(user_id, str) or not user_id.strip():
            raise FileHandoffWriteError()
        if not isinstance(session_id, str) or not session_id.strip():
            raise FileHandoffWriteError()

        root = self._root_dir.resolve(strict=False)
        safe_name = sanitize_document_filename(
            f"{_safe_stem(filename)}{canonical_suffix}"
        )
        target_dir = session_root_for(
            temp_root=root,
            user_id=user_id,
            session_id=session_id,
        ).resolve(strict=False)

        try:
            target_dir.relative_to(root)
        except ValueError:
            raise FileHandoffWriteError() from None

        target_dir.mkdir(parents=True, exist_ok=True)

        target = (target_dir / f"{uuid.uuid4().hex}-{safe_name}").resolve(strict=False)
        try:
            target.relative_to(target_dir)
        except ValueError:
            raise FileHandoffWriteError() from None

        log_event(
            "document file handoff target",
            user_id=user_id,
            session_id=session_id,
            target_path=str(target),
            original_file_name=filename,
        )
        return target


def is_allowed_handoff_suffix(suffix: Optional[str]) -> bool:
    """
    检查文件交接后缀是否属于允许列表。

    Args:
    - suffix: 待校验的文件后缀。

    Return:
    - bool: 后缀允许时返回 True，否则返回 False。
    """
    if not suffix:
        return False
    return suffix.lower() in _ALLOWED_HANDOFF_SUFFIXES


def _validate_suffix(suffix: str) -> str:
    """校验当前流程。"""
    if not isinstance(suffix, str):
        raise FileHandoffInvalidSuffixError("non_string_suffix")

    normalized = suffix.lower()
    if not normalized.startswith(".") or normalized not in _ALLOWED_HANDOFF_SUFFIXES:
        raise FileHandoffInvalidSuffixError(suffix)

    return normalized


def _safe_stem(filename: str) -> str:
    """处理当前流程。"""
    if not isinstance(filename, str):
        raise FileHandoffWriteError()

    base = PurePosixPath(filename.replace("\\", "/")).name
    stem = PurePosixPath(base).stem
    stem_path = PurePosixPath(stem)

    if stem_path.suffix.lower() in _DANGEROUS_INNER_SUFFIXES:
        stem = stem_path.stem

    safe = re.sub(r"[^A-Za-z0-9._\-\u4e00-\u9fff]+", "_", stem).strip("._-")
    return safe or "document"

