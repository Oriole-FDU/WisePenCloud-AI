"""
Scheduler — wraps ContainerQueue with retry, load balancing, and anti-starvation.
"""
from __future__ import annotations

import threading
import time

from common.core.exceptions import ServiceException
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

    def acquire(self, user_id: str, session_id: str) -> tuple[str, int]:
        """获取容器，返回 (container_id, fencing_token)。"""
        if self._user_allocations.get(user_id, set()).__len__() >= self._session_max:
            raise ServiceException(
                SandboxErrorCode.QUEUE_FULL,
                f"user {user_id} already holds {self._session_max} containers",
            )

        deadline = time.time() + self._timeout
        while True:
            result = self._try_acquire(user_id, session_id)
            if result:
                return result
            if time.time() >= deadline:
                break
            with self._condition:
                self._condition.wait(timeout=self._retry_interval)

        raise ServiceException(SandboxErrorCode.QUEUE_NO_IDLE, 
            total=len(self._queue.containers),
            max_total=self._queue._max_total,
        )

    def release(self, container_id: str, fencing_token: int = 0) -> None:
        """释放容器并通知等待者。"""
        user_id = ""
        with self._queue.lock:
            info = self._queue.containers.get(container_id)
            if info:
                user_id = info.user_id
        self._queue.release(container_id, fencing_token)
        if user_id:
            self._user_allocations.get(user_id, set()).discard(container_id)
        with self._condition:
            self._condition.notify_all()

    def _try_acquire(self, user_id: str, session_id: str) -> tuple[str, int] | None:
        """Attempt to get an idle container via queue.acquire (uses fencing token)."""
        cid, token = self._queue.acquire(user_id, session_id)
        self._user_allocations.setdefault(user_id, set()).add(cid)
        _dbg("acquired", cid=cid[:12], token=token, user=user_id)
        return cid, token

    def health_check(self) -> dict:
        return {
            **self._queue.health_check(),
            "waiting": sum(len(s) for s in self._user_allocations.values()),
            "users_with_containers": len(self._user_allocations),
        }

    @property
    def queue(self) -> ContainerQueue:
        return self._queue
