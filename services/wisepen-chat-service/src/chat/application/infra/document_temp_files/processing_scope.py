from contextlib import contextmanager
from pathlib import Path
from typing import Iterator
from uuid import uuid4

from common.logger import log_error


@contextmanager
def document_processing_scope(session_root: Path) -> Iterator[None]:
    """
    文档处理临时保护作用域。

    - 进入作用域时，在 session_root/.in_progress 下创建一个临时 marker。
    - 外部 cleanup 任务可通过 .in_progress 判断该 session 正在处理，避免误删。
    - 退出作用域时删除当前 marker；如果目录为空，则顺手删除 .in_progress。
    - 这是 best-effort processing marker，不是并发锁。
    """
    marker_dir = session_root / ".in_progress"
    marker_path = marker_dir / f"{uuid4().hex}.lock"

    # 创建当前处理任务的 marker。
    # 同一 session 并发处理时，每个任务使用独立 uuid 文件，避免互相覆盖。
    try:
        marker_dir.mkdir(parents=True, exist_ok=True)
        marker_path.write_text("", encoding="utf-8")
    except OSError as exc:
        # marker 创建失败不阻断主处理链路，只记录日志。
        log_error(
            "document temp processing marker create",
            exc,
            session_root=str(session_root),
            marker_path=str(marker_path),
        )

    try:
        yield
    finally:
        # 退出处理作用域后，尽量清理自己的 marker。
        # rmdir 只会删除空目录；如果还有其他 marker，说明仍有并发任务在处理。
        try:
            marker_path.unlink(missing_ok=True)
            marker_dir.rmdir()
        except OSError:
            pass