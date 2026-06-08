import asyncio
from typing import Optional, Set

from common.logger import log_fail, log_ok
from .cleanup import (
    DOCUMENT_TEMP_FILE_TTL_SECONDS,
    DocumentTempFileCleanupService,
)


class DocumentTempFileCleanupScheduler:
    def __init__(
        self,
        cleanup_service: DocumentTempFileCleanupService,
        interval_seconds: int = DOCUMENT_TEMP_FILE_TTL_SECONDS,
        initial_delay_seconds: int = 60,
    ) -> None:
        self._cleanup_service = cleanup_service
        self._interval_seconds = interval_seconds
        self._initial_delay_seconds = initial_delay_seconds
        self._task: Optional[asyncio.Task[None]] = None
        self._background_tasks: Set[asyncio.Task[None]] = set()

    def start(self) -> None:
        if self._task is not None and not self._task.done():
            return

        task = asyncio.create_task(self._run_loop())
        self._task = task
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    async def close(self) -> None:
        if self._task is None:
            return

        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        finally:
            self._task = None

    async def _run_loop(self) -> None:
        if self._initial_delay_seconds > 0:
            await asyncio.sleep(self._initial_delay_seconds)

        while True:
            try:
                result = await asyncio.to_thread(self._cleanup_service.cleanup)
                log_ok(
                    "document_temp_file_cleanup_scheduler",
                    scanned_session_dirs=result.scanned_session_dirs,
                    removed_session_dirs=result.removed_session_dirs,
                    failed_session_dirs=result.failed_session_dirs,
                    skipped_in_progress_dirs=result.skipped_in_progress_dirs,
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log_fail(
                    "document_temp_file_cleanup_scheduler",
                    f"Document temp file cleanup failed: {repr(exc)}",
                )

            await asyncio.sleep(self._interval_seconds)
