"""File system abstraction for sandbox operations.

All tools depend on FileSystemProvider rather than concrete implementations,
allowing the underlying storage to be swapped without changing tool code.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional


class FileSystemProvider(ABC):
    """Abstract file system + shell operations for sandbox workspaces."""

    @abstractmethod
    async def read_file(
        self, file_path: str, max_chars: Optional[int] = None,
        user_id: str = "", session_id: str = "",
    ) -> str: ...

    @abstractmethod
    async def write_file(
        self, file_path: str, content: str,
        user_id: str = "", session_id: str = "",
    ) -> Dict[str, Any]: ...

    @abstractmethod
    async def list_directory(
        self, path: str, recursive: bool = False,
        user_id: str = "", session_id: str = "",
    ) -> List[Dict[str, Any]]: ...

    @abstractmethod
    async def grep_files(
        self, path: str, pattern: str,
        recursive: bool = True, ignore_case: bool = False,
        user_id: str = "", session_id: str = "",
    ) -> List[Dict[str, Any]]: ...

    @abstractmethod
    async def replace_in_file(
        self, file_path: str, old_str: str, new_str: str,
        user_id: str = "", session_id: str = "",
    ) -> Dict[str, Any]: ...

    @abstractmethod
    async def shell_exec(
        self, command: str, exec_dir: str = "/workspace",
        timeout_ms: int = 30000,
        user_id: str = "", session_id: str = "",
    ) -> Dict[str, Any]: ...
