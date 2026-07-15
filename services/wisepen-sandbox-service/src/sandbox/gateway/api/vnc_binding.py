"""
ContainerBinding — 维护 (user_id, session_id) → container_id 映射。

实现 SandboxEndpoint 协议，网关不感知 Docker / 容器队列细节。
"""
from __future__ import annotations

import threading
import time

from sandbox.Queue.pool_manager import ContainerPoolManager
from sandbox.gateway.container_utils import container_url, container_ws_url
from sandbox.gateway.sandbox_endpoint import SandboxEndpoint, SandboxConnection


class ContainerBinding(SandboxEndpoint):
    """用户+会话 → 容器绑定管理器，实现 SandboxEndpoint 协议。"""

    def __init__(self, pool: ContainerPoolManager):
        self._pool = pool
        self._bindings: dict[tuple[str, str], str] = {}  # (uid, sid) → cid
        self._last_access: dict[tuple[str, str], float] = {}  # heartbeat
        self._lock = threading.Lock()

    # ---- SandboxEndpoint protocol ----

    def acquire(self, user_id: str, session_id: str) -> SandboxConnection:
        """获取已有绑定或从池中分配新容器，返回连接信息。"""
        cid = self._get_or_alloc(user_id, session_id)
        base = container_url(cid)
        return SandboxConnection(
            vnc_url=f"{base}/vnc/index.html?autoconnect=true",
            websockify_url=container_ws_url(cid),
            container_id=cid[:12],
            metadata={"host_port": base.rsplit(":", 1)[-1]},
        )

    def release(self, user_id: str, session_id: str) -> None:
        """释放绑定，归还容器到池。"""
        key = (user_id, session_id)
        with self._lock:
            cid = self._bindings.pop(key, None)
            self._last_access.pop(key, None)
        if cid:
            self._pool.release(cid, user_id, session_id)

    def stats(self) -> dict:
        with self._lock:
            return {"active_bindings": len(self._bindings),
                    "bindings": {f"{u}:{s}": c[:12] for (u, s), c in self._bindings.items()}}

    # ---- internal ----

    def _get_or_alloc(self, user_id: str, session_id: str) -> str:
        key = (user_id, session_id)
        with self._lock:
            if key in self._bindings:
                self._last_access[key] = time.time()
                return self._bindings[key]
        cid = self._pool.acquire(user_id, session_id)
        with self._lock:
            self._bindings[key] = cid
            self._last_access[key] = time.time()
        return cid

    def heartbeat(self, user_id: str, session_id: str) -> None:
        key = (user_id, session_id)
        with self._lock:
            if key in self._last_access:
                self._last_access[key] = time.time()

    def cleanup_idle(self, idle_timeout: float = 1800.0) -> int:
        now = time.time()
        with self._lock:
            expired = [(k, self._bindings[k]) for k, last in self._last_access.items()
                       if now - last >= idle_timeout and k in self._bindings]
            for (uid, sid), _ in expired:
                self._bindings.pop((uid, sid), None)
                self._last_access.pop((uid, sid), None)
        released = 0
        for (uid, sid), cid in expired:
            self._pool.release(cid, uid, sid)
            released += 1
        return released
