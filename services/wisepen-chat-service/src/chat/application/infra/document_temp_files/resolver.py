import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from common.logger import log_event
from .errors import (
    InvalidDocumentRefError,
    UnreadableDocumentRefError,
)
from .path import session_root_for

DOCUMENT_TEMP_FILE_ROOT = Path(tempfile.gettempdir()) / "wisepen-chat-upload-files"
DOCUMENT_TEMP_FILE_MAX_BYTES = 50 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class ResolvedDocumentSource:
    path: Path
    user_id: str
    session_id: str
    source_file_name: str
    size_bytes: int
    content_type: Optional[str] = None


@dataclass(frozen=True, slots=True)
class DocumentTempFileResolver:
    """
    file_ref 临时文件解析器。

    - 只允许解析当前 user_id / session_id 隔离目录内的文件。
    - 防止路径穿越和符号链接逃逸。
    - 返回文档系统可直接读取的 ResolvedDocumentSource。
    """

    temp_root: Path = DOCUMENT_TEMP_FILE_ROOT
    max_file_size_bytes: Optional[int] = DOCUMENT_TEMP_FILE_MAX_BYTES

    def resolve(
        self,
        *,
        file_ref: str,
        user_id: str,
        session_id: str,
        content_type: Optional[str] = None,
    ) -> ResolvedDocumentSource:
        # file_ref / user_id / session_id 都来自调用边界，必须存在。
        if not file_ref or not str(file_ref).strip():
            raise InvalidDocumentRefError()
        if not user_id or not str(user_id).strip():
            raise InvalidDocumentRefError()
        if not session_id or not str(session_id).strip():
            raise InvalidDocumentRefError()

        root = self._resolve_root()
        session_root = self._candidate_session_root(
            root=root,
            user_id=user_id,
            session_id=session_id,
        )

        # 相对 file_ref 默认解析到当前 session 目录下。
        # 绝对路径也允许进入后续安全校验，但必须最终位于 session_root 内。
        candidate = Path(file_ref)
        if not candidate.is_absolute():
            candidate = session_root / candidate

        # 第一层：词法路径检查。
        # 在 resolve 符号链接前，先拦截明显的 ../ 路径穿越。
        try:
            lexical_path = Path(os.path.abspath(candidate))
        except OSError as e:
            raise UnreadableDocumentRefError() from e

        try:
            lexical_path.relative_to(session_root)
        except ValueError as e:
            raise InvalidDocumentRefError() from e

        self._ensure_session_root_exists(session_root)

        # 第二层：真实路径检查。
        # resolve(strict=True) 会解析符号链接，并要求文件真实存在。
        try:
            resolved_path = candidate.resolve(strict=True)
        except FileNotFoundError as e:
            raise UnreadableDocumentRefError() from e
        except OSError as e:
            raise UnreadableDocumentRefError() from e

        # 防止符号链接把路径带出当前 session_root。
        try:
            resolved_path.relative_to(session_root)
        except ValueError as e:
            raise InvalidDocumentRefError() from e

        # 文件可读性与大小检查。
        try:
            if resolved_path.is_dir():
                raise UnreadableDocumentRefError()
            if not resolved_path.is_file():
                raise UnreadableDocumentRefError()
            if not os.access(resolved_path, os.R_OK):
                raise UnreadableDocumentRefError()
            size_bytes = resolved_path.stat().st_size
        except OSError as e:
            raise UnreadableDocumentRefError() from e

        if (
            self.max_file_size_bytes is not None
            and size_bytes > self.max_file_size_bytes
        ):
            raise UnreadableDocumentRefError()

        log_event(
            "document_temp_files resolved",
            user_id=user_id,
            session_id=session_id,
            path=str(resolved_path),
            size_bytes=size_bytes,
        )

        return ResolvedDocumentSource(
            path=resolved_path,
            user_id=user_id,
            session_id=session_id,
            source_file_name=resolved_path.name,
            size_bytes=size_bytes,
            content_type=content_type,
        )

    def session_root(self, *, user_id: str, session_id: str) -> Path:
        """
        返回当前 user/session 的临时文件根目录。

        - 用于写入侧获取隔离目录。
        - 返回前会确认目录已经存在且确实是目录。
        """
        root = self._resolve_root()
        session_root = self._candidate_session_root(
            root=root,
            user_id=user_id,
            session_id=session_id,
        )
        self._ensure_session_root_exists(session_root)
        return session_root

    def _resolve_root(self) -> Path:
        """
        解析并确认全局临时文件根目录存在。
        """
        try:
            root = self.temp_root.resolve(strict=False)
        except OSError as e:
            raise UnreadableDocumentRefError() from e

        if not root.exists():
            raise UnreadableDocumentRefError()
        if not root.is_dir():
            raise UnreadableDocumentRefError()
        return root

    def _candidate_session_root(
        self,
        *,
        root: Path,
        user_id: str,
        session_id: str,
    ) -> Path:
        """
        构造当前 user/session 的候选目录，并确保它没有逃逸 temp_root。
        """
        try:
            session_root = session_root_for(
                temp_root=root,
                user_id=user_id,
                session_id=session_id,
            ).resolve(strict=False)
        except OSError as e:
            raise UnreadableDocumentRefError() from e

        try:
            session_root.relative_to(root)
        except ValueError as e:
            raise InvalidDocumentRefError() from e

        return session_root

    def _ensure_session_root_exists(self, session_root: Path) -> Path:
        """
        确认 session_root 已存在且是目录。
        """
        if not session_root.exists():
            raise UnreadableDocumentRefError()
        if not session_root.is_dir():
            raise UnreadableDocumentRefError()
        return session_root



