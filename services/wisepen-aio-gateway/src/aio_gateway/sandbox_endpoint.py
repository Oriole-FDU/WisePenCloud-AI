"""
SandboxEndpoint — 抽象网关与沙箱后端的连接协议。

网关不感知 Docker / AIO / 容器队列细节，只通过此接口获取用户专属沙箱地址。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class SandboxConnection:
    """沙箱为某个用户+会话提供的连接信息。"""
    vnc_url: str                 # noVNC 页面地址
    websockify_url: str          # WebSocket 地址 (ws://...)
    container_id: str            # 内部标识（日志/释放用）
    metadata: dict[str, str]     # 自定义扩展 {key: value}


class SandboxEndpoint(ABC):
    """沙箱端点协议——任何沙箱后端实现此接口即可接入网关。"""

    @abstractmethod
    def acquire(self, user_id: str, session_id: str) -> SandboxConnection:
        """为用户+会话预分配一个沙箱，返回连接信息。"""
        ...

    @abstractmethod
    def release(self, user_id: str, session_id: str) -> None:
        """释放用户+会话的沙箱连接。"""
        ...

    @abstractmethod
    def stats(self) -> dict:
        """返回当前沙箱池统计信息。"""
        ...
