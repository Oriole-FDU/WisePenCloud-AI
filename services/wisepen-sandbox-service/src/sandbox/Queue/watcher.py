"""
Watcher — background thread that maintains container queue health.

Runs in a daemon thread alongside the HTTP server:
- Health checks containers, marks dead
- Removes dead containers from queue
- Ensures minimum idle count (prefetch)
- Recycles dirty containers after TTL
- Cleans stale workspace cache directories on host
"""
from __future__ import annotations

import os
import shutil
import threading
import time

from sandbox.Queue.container_queue import ContainerQueue, ContainerState
from sandbox.Queue.file_manager import FileManager
from sandbox.core.debug import debug

_dbg = debug("[SANDBOX][watcher]")


class Watcher:
    """Background maintainer for the container queue."""

    def __init__(
        self,
        queue: ContainerQueue,
        health_interval: float = 10.0,
        prefetch_interval: float = 5.0,
        recycle_interval: float = 30.0,
        dirty_ttl: float = 60.0,
        workspace_cache: str = "/workspaces",
        workspace_cleanup_ttl: float = 7 * 24 * 3600,
        workspace_cleanup_interval: float = 3600.0,
        workspace_store=None,  # WorkspaceStore | None
        file_manager: FileManager | None = None,
    ):
        self._queue = queue
        self._health_interval = health_interval
        self._prefetch_interval = prefetch_interval
        self._recycle_interval = recycle_interval
        self._dirty_ttl = dirty_ttl
        self._workspace_cache = workspace_cache
        self._workspace_cleanup_ttl = workspace_cleanup_ttl
        self._workspace_cleanup_interval = workspace_cleanup_interval
        self._workspace_store = workspace_store
        self._file_manager = file_manager
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        """Start the watcher daemon thread."""
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="aio-watcher")
        self._thread.start()
        _dbg("watcher_started")

    def stop(self) -> None:
        """Signal the watcher to stop."""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=10)
        _dbg("watcher_stopped")

    def _run(self) -> None:
        last_health = 0.0
        last_prefetch = 0.0
        last_recycle = 0.0
        last_workspace_cleanup = 0.0
        last_lease_recovery = 0.0
        last_checkpoint = 0.0

        while not self._stop_event.is_set():
            now = time.time()

            if now - last_health >= self._health_interval:
                self._do_health()
                last_health = now

            if now - last_prefetch >= self._prefetch_interval:
                self._do_prefetch()
                last_prefetch = now

            if now - last_recycle >= self._recycle_interval:
                self._do_recycle()
                last_recycle = now

            if now - last_lease_recovery >= 120.0:
                self._do_lease_recovery()
                last_lease_recovery = now

            if now - last_checkpoint >= 300.0:  # 每 5 分钟检查点
                self._do_checkpoint()
                last_checkpoint = now

            if now - last_workspace_cleanup >= self._workspace_cleanup_interval:
                self._do_workspace_cleanup()
                last_workspace_cleanup = now

            time.sleep(1)

    def _do_health(self) -> None:
        try:
            summary = self._queue.health_check()
            dead = summary.get("dead", 0)
            if dead > 0:
                removed = self._queue.remove_dead()
                _dbg("health_removed_dead", dead_found=dead, removed=removed)
            #elif _DEBUG:
            #    _dbg("health_ok", **summary)
        except Exception as e:
            _dbg("health_error", error=str(e))

    def _do_prefetch(self) -> None:
        try:
            created_containers = self._queue.ensure_idle_count()
            if created_containers > 0:
                _dbg("prefetch_created", count=created_containers, total=self._queue.total_containers)
        except Exception as e:
            _dbg("prefetch_error", error=str(e))

    def _do_recycle(self) -> None:
        try:
            # Find dirty containers older than TTL
            now = time.time()
            dirty_cids = []
            with self._queue.lock:
                for cid, info in self._queue.containers.items():
                    if info.state == ContainerState.DIRTY:
                        age = now - info.allocated_at if info.allocated_at else 999
                        if age >= self._dirty_ttl:
                            dirty_cids.append(cid)

            for cid in dirty_cids:
                new_cid = self._queue.recycle(cid)
                if new_cid:
                    _dbg("recycled", old_cid=cid[:12], new_cid=new_cid[:12])
        except Exception as e:
            _dbg("recycle_error", error=str(e))

    def _do_lease_recovery(self) -> None:
        """回收超过 TTL 仍处于 BUSY 状态的容器（调用者忘记释放）。"""
        try:
            now = time.time()
            expired: list[tuple[str, int]] = []
            with self._queue.lock:
                for cid, info in self._queue.containers.items():
                    if info.state == ContainerState.BUSY and info.lease_expires_at > 0 and now >= info.lease_expires_at:
                        expired.append((cid, info.fencing_token))
            for cid, token in expired:
                self._queue.release(cid, token)
                _dbg("lease_expired", cid=cid[:12], token=token)
        except Exception as e:
            _dbg("lease_recovery_error", error=str(e))

    def _do_workspace_cleanup(self) -> None:
        """Remove stale workspace data, using store abstraction if available."""
        try:
            if self._workspace_store:
                stale = self._workspace_store.list_all_stale(self._workspace_cleanup_ttl)
                removed = 0
                for uid, sid in stale:
                    self._workspace_store.delete(uid, sid)
                    removed += 1
                if removed > 0:
                    _dbg("workspace_cleanup_done", removed=removed)
                return

            # 回退：本地文件系统直接操作
            root = self._workspace_cache
            if not os.path.isdir(root):
                return
            removed = 0
            for entry in os.listdir(root):
                user_dir = os.path.join(root, entry)
                if not os.path.isdir(user_dir):
                    continue
                for sess_name in os.listdir(user_dir):
                    sess_dir = os.path.join(user_dir, sess_name)
                    if not os.path.isdir(sess_dir):
                        continue
                    try:
                        mtime = os.path.getmtime(sess_dir)
                        if time.time() - mtime >= self._workspace_cleanup_ttl:
                            shutil.rmtree(sess_dir, ignore_errors=True)
                            removed += 1
                            _dbg("workspace_removed", path=sess_dir)
                    except OSError:
                        pass
            for entry in os.listdir(root):
                user_dir = os.path.join(root, entry)
                try:
                    if os.path.isdir(user_dir) and not os.listdir(user_dir):
                        os.rmdir(user_dir)
                except OSError:
                    pass
            if removed > 0:
                _dbg("workspace_cleanup_done", removed=removed)
        except Exception as e:
            _dbg("workspace_cleanup_error", error=str(e))

    def _do_checkpoint(self) -> None:
        """对活跃 BUSY 容器执行定期检查点（自动保存不释放）。"""
        if not self._file_manager:
            return
        try:
            with self._queue.lock:
                busy = [(cid, info) for cid, info in self._queue.containers.items()
                        if info.state == ContainerState.BUSY]
            for cid, info in busy:
                if self._file_manager.should_checkpoint(info.user_id, info.session_id):
                    if self._file_manager.checkpoint(cid, info.user_id, info.session_id):
                        _dbg("checkpoint", cid=cid[:12], user=info.user_id)
        except Exception as e:
            _dbg("checkpoint_error", error=str(e))
