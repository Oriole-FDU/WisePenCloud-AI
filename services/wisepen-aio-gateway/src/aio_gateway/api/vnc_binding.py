"""
ContainerBinding — 维护 (user_id, session_id) → container_id 映射。

VNC 浏览器会话需要持久绑定到同一容器。首次访问时从容器池分配，
后续 WebSocket 帧复用已有绑定。关闭时归还容器。
"""
from __future__ import annotations

import threading
import time

from sandbox.Queue.pool_manager import ContainerPoolManager


class ContainerBinding:
    """用户+会话 → 容器绑定管理器，线程安全。"""

    def __init__(self, pool: ContainerPoolManager):
        self._pool = pool
        self._bindings: dict[tuple[str, str], str] = {}  # (uid, sid) → cid
        self._last_access: dict[tuple[str, str], float] = {}  # heartbeat
        self._lock = threading.Lock()

    def acquire(self, user_id: str, session_id: str) -> str:
        """获取已有绑定或从池中分配新容器。"""
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

    def lookup(self, user_id: str, session_id: str) -> str | None:
        """查找已有绑定，不分配新容器。"""
        key = (user_id, session_id)
        with self._lock:
            cid = self._bindings.get(key)
            if cid:
                self._last_access[key] = time.time()
            return cid

    def release(self, user_id: str, session_id: str) -> None:
        """释放绑定，归还容器到池。"""
        key = (user_id, session_id)
        with self._lock:
            cid = self._bindings.pop(key, None)
            self._last_access.pop(key, None)
        if cid:
            self._pool.release(cid, user_id, session_id)

    def heartbeat(self, user_id: str, session_id: str) -> None:
        """更新心跳时间。"""
        key = (user_id, session_id)
        with self._lock:
            if key in self._last_access:
                self._last_access[key] = time.time()

    def cleanup_idle(self, idle_timeout: float = 1800.0) -> int:
        """释放所有超过 idle_timeout 秒无心跳的绑定。返回释放数。"""
        now = time.time()
        # 快照过期 key，在锁内完成以避免 TOCTOU
        with self._lock:
            expired = [(k, self._bindings[k]) for k, last in self._last_access.items()
                       if now - last >= idle_timeout and k in self._bindings]
            for (uid, sid), _ in expired:
                self._bindings.pop((uid, sid), None)
                self._last_access.pop((uid, sid), None)
        # 锁外归还容器
        released = 0
        for (uid, sid), cid in expired:
            self._pool.release(cid, uid, sid)
            released += 1
        return released

    def stats(self) -> dict:
        """返回当前绑定统计。"""
        with self._lock:
            return {"active_bindings": len(self._bindings),
                    "bindings": {f"{u}:{s}": c[:12] for (u, s), c in self._bindings.items()}}
