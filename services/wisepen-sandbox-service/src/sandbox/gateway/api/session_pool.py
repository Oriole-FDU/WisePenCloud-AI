"""SessionPool — 统一的 (user_id, session_id) → (container_id, token) 会话亲和性映射。

实现 SandboxEndpoint 协议，VNC 和文件/Shell/MCP 均通过 SandboxConnection 获取容器信息。
"""
from __future__ import annotations

import threading
import time

from sandbox.Queue.pool_manager import ContainerPoolManager
from sandbox.gateway.container_utils import container_url, container_ws_url
from sandbox.gateway.sandbox_endpoint import SandboxEndpoint, SandboxConnection


class SessionPool(SandboxEndpoint):
    """会话 → 容器绑定管理器，实现 SandboxEndpoint 协议。

    VNC 通过 SandboxConnection.vnc_url 获取连接；
    文件/Shell/MCP 通过 SandboxConnection.container_id + fencing_token 操作容器。

    首次请求：从池中分配容器 + pull 工作区
    后续请求：直接复用绑定容器（无 pull/push）
    会话结束：push + release 归还容器
    空闲超时：后台清理释放
    """

    def __init__(self, pool: ContainerPoolManager, idle_timeout: float = 1800.0):
        self._pool = pool
        self._idle_timeout = idle_timeout
        self._bindings: dict[tuple[str, str], tuple[str, int]] = {}  # (uid,sid) → (cid,token)
        self._last_access: dict[tuple[str, str], float] = {}
        self._lock = threading.Lock()

    # ---- SandboxEndpoint 协议 ----

    def acquire(self, user_id: str, session_id: str) -> SandboxConnection:
        """获取已有绑定或从池中分配新容器。首次分配时自动 pull 工作区。

        返回 SandboxConnection：
          - VNC 端点使用 vnc_url / websockify_url
          - 文件/Shell/MCP 使用 container_id + fencing_token
        """
        key = (user_id, session_id)
        with self._lock:
            if key in self._bindings:
                self._last_access[key] = time.time()
                cid, token = self._bindings[key]
                return self._build_connection(cid, token)

        cid, token = self._pool.acquire(user_id, session_id)  # Scheduler + pull
        with self._lock:
            self._bindings[key] = (cid, token)
            self._last_access[key] = time.time()
        return self._build_connection(cid, token)

    def release(self, user_id: str, session_id: str) -> None:
        """显式释放会话，push 工作区并归还容器。"""
        key = (user_id, session_id)
        with self._lock:
            binding = self._bindings.pop(key, None)
            self._last_access.pop(key, None)
        if binding:
            cid, token = binding
            self._pool.release(cid, user_id, session_id, token)

    def stats(self) -> dict:
        with self._lock:
            return {
                "active_bindings": len(self._bindings),
                "sessions": {f"{u}:{s}": cid[:12] for (u, s), (cid, _) in self._bindings.items()},
            }

    # ---- 会话生命周期 ----

    def heartbeat(self, user_id: str, session_id: str) -> None:
        """更新最后活跃时间（每次文件/Shell/MCP 请求调用）。"""
        with self._lock:
            if (user_id, session_id) in self._last_access:
                self._last_access[(user_id, session_id)] = time.time()

    def cleanup_idle(self) -> int:
        """释放空闲超时的会话容器。返回释放数量。"""
        now = time.time()
        expired_bindings: list[tuple[str, str, str, int]] = []
        with self._lock:
            expired_keys = [k for k, last in self._last_access.items()
                           if now - last >= self._idle_timeout and k in self._bindings]
            for k in expired_keys:
                cid, token = self._bindings.pop(k)
                self._last_access.pop(k)
                expired_bindings.append((k[0], k[1], cid, token))

        released = 0
        for uid, sid, cid, token in expired_bindings:
            try:
                self._pool.release(cid, uid, sid, token)
                released += 1
            except Exception:
                pass
        return released

    # ---- internal ----

    def _build_connection(self, cid: str, token: int) -> SandboxConnection:
        base = container_url(cid)
        return SandboxConnection(
            container_id=cid,
            short_id=cid[:12],
            fencing_token=token,
            vnc_url=f"{base}/vnc/index.html?autoconnect=true",
            websockify_url=container_ws_url(cid),
            metadata={"host_port": base.rsplit(":", 1)[-1]},
        )
