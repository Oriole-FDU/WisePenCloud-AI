from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

from common.core.exceptions import ServiceException

from sandbox.application.services.sandbox_session import SandboxSessionService
from sandbox.domain.error_codes import SandboxErrorCode


@dataclass(frozen=True)
class VncConnection:
    vnc_url: str
    sandbox_id: str
    websocket_url: str | None = None


class VncBinding:
    def __init__(self, session: SandboxSessionService, idle_timeout_seconds: float = 1800.0) -> None:
        self._session = session
        self._idle_timeout = idle_timeout_seconds
        # 绑定键使用 user_id + session_id，与 Scheduler 的 tenant/workspace 维度保持一致。
        self._bindings: dict[tuple[str, str], tuple[VncConnection, float]] = {}
        self._lock = asyncio.Lock()

    async def acquire(self, user_id: str, session_id: str) -> VncConnection:
        key = (user_id, session_id)
        async with self._lock:
            current = self._bindings.get(key)
            if current is not None:
                connection, _ = current
                # 每次访问刷新空闲时间，避免正在使用的 VNC 被 cleanup_idle 回收。
                self._bindings[key] = (connection, time.monotonic())
                return connection
            lease = await self._session.acquire_for(user_id, session_id)
            if lease.endpoint is None or not lease.endpoint.public_vnc_url:
                raise ServiceException(
                    SandboxErrorCode.SANDBOX_UNAVAILABLE,
                    "public VNC URL 未配置",
                )
            connection = VncConnection(
                vnc_url=lease.endpoint.public_vnc_url,
                sandbox_id=lease.sandbox_id,
                websocket_url=lease.endpoint.public_websocket_url,
            )
            self._bindings[key] = (connection, time.monotonic())
            return connection

    async def release(self, user_id: str, session_id: str) -> None:
        key = (user_id, session_id)
        async with self._lock:
            removed = self._bindings.pop(key, None)
        if removed is not None:
            await self._session.release_for(user_id, session_id)

    async def cleanup_idle(self) -> int:
        cutoff = time.monotonic() - self._idle_timeout
        async with self._lock:
            expired = [key for key, (_, last_access) in self._bindings.items() if last_access <= cutoff]
            for key in expired:
                self._bindings.pop(key, None)

        released = 0
        for user_id, session_id in expired:
            try:
                # 释放放在锁外执行，避免 Scheduler/Docker 慢调用阻塞新的远程桌面获取。
                await self._session.release_for(user_id, session_id)
            except Exception:
                continue
            released += 1
        return released

    async def stats(self) -> dict[str, object]:
        async with self._lock:
            return {
                "active_bindings": len(self._bindings),
                "bindings": {
                    f"{user_id}:{session_id}": connection.sandbox_id
                    for (user_id, session_id), (connection, _) in self._bindings.items()
                },
            }
