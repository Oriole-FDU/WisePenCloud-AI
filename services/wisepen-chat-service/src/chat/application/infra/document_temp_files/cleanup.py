import shutil
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from common.logger import log_error, log_event, log_fail

DOCUMENT_TEMP_FILE_ROOT = Path(tempfile.gettempdir()) / "wisepen-chat-upload-files"
DOCUMENT_TEMP_FILE_TTL_SECONDS = 6 * 60 * 60
DOCUMENT_TEMP_FILE_GRACE_SECONDS = 10 * 60


@dataclass(frozen=True, slots=True)
class CleanupResult:
    scanned_session_dirs: int
    removed_session_dirs: int
    failed_session_dirs: int
    skipped_in_progress_dirs: int


@dataclass(frozen=True, slots=True)
class DocumentTempFileCleanupService:
    """
    文档临时文件清理服务。

    - 按 user/session 目录扫描临时文件根目录。
    - 只清理超过 TTL + grace 的 session 目录。
    - 如果 session 下存在 .in_progress marker，则跳过，避免误删正在处理的文件。
    """

    temp_root: Path = DOCUMENT_TEMP_FILE_ROOT
    ttl_seconds: int = DOCUMENT_TEMP_FILE_TTL_SECONDS
    grace_seconds: int = DOCUMENT_TEMP_FILE_GRACE_SECONDS

    def cleanup(self) -> CleanupResult:
        # 解析全局临时文件根目录。
        try:
            root = self.temp_root.resolve(strict=False)
        except OSError as e:
            log_error(
                "document temp cleanup root resolve",
                e,
                temp_root=str(self.temp_root),
            )
            return CleanupResult(0, 0, 1, 0)

        # 根目录不存在时无需清理。
        if not root.exists():
            log_event("document_temp_cleanup skipped", reason="root_missing", root=str(root))
            return CleanupResult(0, 0, 0, 0)

        # 根路径异常时直接失败，避免误删非目录路径。
        if not root.is_dir():
            log_fail("document_temp_cleanup", "root_not_directory", root=str(root))
            return CleanupResult(0, 0, 1, 0)

        # 清理阈值：
        # - ttl_seconds 是文件保留时间。
        # - grace_seconds 是额外宽限期，避免刚结束或正在边界处理的 session 被误删。
        cutoff = time.time() - self.ttl_seconds - self.grace_seconds

        scanned = 0
        removed = 0
        failed = 0
        skipped_in_progress = 0

        # 目录结构：
        # root / user_id / session_id
        for user_dir in list(root.iterdir()):
            if not user_dir.is_dir():
                continue

            for session_dir in list(user_dir.iterdir()):
                if not session_dir.is_dir():
                    continue

                scanned += 1

                try:
                    # 解析真实路径，并确保 session_dir 没有通过符号链接逃逸 root。
                    resolved_session_dir = session_dir.resolve(strict=True)
                    resolved_session_dir.relative_to(root)

                    # 如果存在进行中 marker，说明该 session 仍在处理，跳过清理。
                    marker_dir = resolved_session_dir / ".in_progress"
                    if marker_dir.exists() and any(marker_dir.iterdir()):
                        skipped_in_progress += 1
                        continue

                    # 未过期的 session 目录不清理。
                    if resolved_session_dir.stat().st_mtime > cutoff:
                        continue

                    # 删除整个 session 临时目录。
                    shutil.rmtree(resolved_session_dir)
                    removed += 1

                except Exception as e:
                    failed += 1
                    log_error(
                        "document temp cleanup session",
                        e,
                        root=str(root),
                        session_dir=str(session_dir),
                    )

        log_event(
            "document_temp_cleanup completed",
            root=str(root),
            scanned_session_dirs=scanned,
            removed_session_dirs=removed,
            failed_session_dirs=failed,
            skipped_in_progress_dirs=skipped_in_progress,
            ttl_seconds=self.ttl_seconds,
            grace_seconds=self.grace_seconds,
        )

        return CleanupResult(scanned, removed, failed, skipped_in_progress)

