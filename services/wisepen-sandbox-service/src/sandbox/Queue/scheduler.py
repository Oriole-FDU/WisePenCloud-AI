"""
Scheduler — wraps ContainerQueue with retry, load balancing, and anti-starvation.
"""
from __future__ import annotations

import threading
import time

from common.sandbox import SandboxException
from sandbox.Queue.container_queue import ContainerQueue, ContainerState
from sandbox.core.debug import debug

_dbg = debug("[SANDBOX][scheduler]")


class Scheduler:
    """Wraps ContainerQueue with allocation policy.

    - FIFO ordering: idle containers used in round-robin order
    - Retry: waits up to allocation_timeout seconds if no idle container
    - Anti-starvation: max session_max containers per user
    """

    def __init__(
        self,
        queue: ContainerQueue,
        allocation_timeout: float = 5.0,
        retry_interval: float = 0.5,
        session_max: int = 3,
    ):
        self._queue = queue
        self._timeout = allocation_timeout
        self._retry_interval = retry_interval
        self._session_max = session_max
        self._user_allocations: dict[str, set[str]] = {}  # user_id -> {cid, ...}
        self._idle_order: list[str] = []
        self._condition = threading.Condition()

    def acquire(self, user_id: str, session_id: str) -> str:
        """Get a container, retrying up to allocation_timeout seconds."""
        # 防饿死: 每用户最多 session_max 个容器
        if self._user_allocations.get(user_id, set()).__len__() >= self._session_max:
            raise SandboxException(
                code=SandboxException.queue_no_idle().code,
                message=f"user {user_id} already holds {self._session_max} containers",
                retryable=False,
            )

        deadline = time.time() + self._timeout
        while True:
            cid = self._try_acquire(user_id, session_id)
            if cid:
                return cid
            if time.time() >= deadline:
                break
            # Wait for a container to become available (release/recycle signals)
            with self._condition:
                self._condition.wait(timeout=self._retry_interval)

        raise SandboxException.queue_no_idle(
            total=len(self._queue.containers),
            max_total=self._queue._max_total,
        )

    def release(self, container_id: str) -> None:
        """Release a container back to the pool and signal waiters."""
        user_id = ""
        with self._queue.lock:
            info = self._queue.containers.get(container_id)
            if info:
                user_id = info.user_id
        self._queue.release(container_id)
        if user_id:
            self._user_allocations.get(user_id, set()).discard(container_id)
        with self._condition:
            self._condition.notify_all()

    def _try_acquire(self, user_id: str, session_id: str) -> str | None:
        """Attempt to get an idle container, preferring FIFO order."""
        with self._queue.lock:
            # Refresh idle_order list — keeps existing order, appends new idle containers
            for cid, info in self._queue.containers.items():
                if info.state == ContainerState.IDLE and cid not in self._idle_order:
                    self._idle_order.append(cid)
            # Remove containers that are no longer idle
            self._idle_order = [
                cid for cid in self._idle_order
                if self._queue.containers.get(cid)
                and self._queue.containers[cid].state == ContainerState.IDLE
            ]

            if not self._idle_order:
                # Try prefetch if under max
                if len(self._queue.containers) < self._queue._max_total:
                    try:
                        cid = self._queue._start_container()
                        info = self._queue.containers.get(cid)
                    except SandboxException:
                        return None
                else:
                    return None

            if not self._idle_order:
                return None

            # FIFO: pop from front
            cid = self._idle_order.pop(0)
            info = self._queue.containers[cid]
            info.state = ContainerState.BUSY
            info.user_id = user_id
            info.session_id = session_id
            info.allocated_at = time.time()
            self._user_allocations.setdefault(user_id, set()).add(cid)
            _dbg("acquired", cid=cid[:12], user=user_id)
            return cid

    def health_check(self) -> dict:
        return {
            **self._queue.health_check(),
            "waiting": sum(len(s) for s in self._user_allocations.values()),
            "users_with_containers": len(self._user_allocations),
        }

    @property
    def queue(self) -> ContainerQueue:
        return self._queue
