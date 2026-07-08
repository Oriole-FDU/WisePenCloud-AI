"""
Watcher — background thread that maintains container queue health.

Runs in a daemon thread alongside the HTTP server:
- Health checks containers, marks dead
- Removes dead containers from queue
- Ensures minimum idle count (prefetch)
- Recycles dirty containers after TTL
"""
from __future__ import annotations

import threading
import time

from sandbox.Queue.container_queue import ContainerQueue, ContainerState
from sandbox.core.debug import debug

_dbg = debug("[SANDBOX][watcher]")


class Watcher:
    """
    Background maintainer for the container queue.

    Runs three loops on configurable intervals:
    - health_interval:  check running state, mark dead
    - prefetch_interval: ensure idle pool size
    - recycle_interval:  clean dirty containers
    """

    def __init__(
        self,
        queue: ContainerQueue,
        health_interval: float = 10.0,
        prefetch_interval: float = 5.0,
        recycle_interval: float = 30.0,
        dirty_ttl: float = 60.0,
    ):
        self._queue = queue
        self._health_interval = health_interval
        self._prefetch_interval = prefetch_interval
        self._recycle_interval = recycle_interval
        self._dirty_ttl = dirty_ttl
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

        while not self._stop_event.is_set():
            now = time.time()

            # 1. Health check
            if now - last_health >= self._health_interval:
                self._do_health()
                last_health = now

            # 2. Ensure idle pool
            if now - last_prefetch >= self._prefetch_interval:
                self._do_prefetch()
                last_prefetch = now

            # 3. Recycle dirty
            if now - last_recycle >= self._recycle_interval:
                self._do_recycle()
                last_recycle = now

            time.sleep(1)

    def _do_health(self) -> None:
        try:
            summary = self._queue.health_check()
            dead = summary.get("dead", 0)
            if dead > 0:
                removed = self._queue.remove_dead()
                _dbg("health_removed_dead", dead_found=dead, removed=removed)
            elif _DEBUG:
                _dbg("health_ok", **summary)
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
