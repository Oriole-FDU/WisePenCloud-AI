"""
工作空间持久化抽象层。

支持两种后端：
- LocalWorkspaceStore: 本地文件系统（默认）
- MongoWorkspaceStore: MongoDB GridFS（生产）

FileManager 通过 WorkspaceStore 接口读写文件，docker cp 负责容器↔主机传输。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class WorkspaceFile:
    """工作空间中的单个文件。"""
    path: str                   # 相对路径，如 "main.py", "outputs/log.txt"
    content: str                # UTF-8 文本内容
    encoding: str = "utf-8"
    size: int = 0               # 字节数


@dataclass
class WorkspaceSnapshot:
    """一次工作空间快照 — 所有文件的集合。"""
    user_id: str
    session_id: str
    files: list[WorkspaceFile] = field(default_factory=list)
    version: int = 0            # 单调递增，用于冲突检测
    updated_at: float = 0.0     # UTC epoch


class WorkspaceStore(ABC):
    """工作空间持久化抽象接口。"""

    @abstractmethod
    def save(self, user_id: str, session_id: str,
             files: list[WorkspaceFile], version: int = 0) -> int:
        """保存文件快照。返回新版本号。"""
        ...

    @abstractmethod
    def load(self, user_id: str, session_id: str) -> WorkspaceSnapshot:
        """加载最新快照。无数据时返回空快照。"""
        ...

    @abstractmethod
    def delete(self, user_id: str, session_id: str) -> None:
        """删除整个工作空间。"""
        ...

    @abstractmethod
    def list_sessions(self, user_id: str) -> list[str]:
        """列出某用户的所有会话 ID。"""
        ...

    @abstractmethod
    def list_all_stale(self, older_than: float) -> list[tuple[str, str]]:
        """列出所有超过 older_than (epoch) 未更新的 (uid, sid)。用于定时清理。"""
        ...
