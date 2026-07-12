"""
HTTP client for the wisepen-sandbox-service file/shell endpoints.
Implements FileSystemProvider ABC — calls sandbox service directly.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, Optional
from urllib.request import Request, urlopen

from common.logger import error as log_error
from chat.core.providers.sandbox.base import FileSystemProvider
from chat.core.config.app_settings import settings


class SandboxProvider(FileSystemProvider):
    """Calls sandbox-service /v1/sandbox/file/* and /v1/sandbox/shell/exec."""

    def __init__(self, base_url: str, from_source: str = "") -> None:
        self._base_url = base_url.rstrip("/")
        self._from_source = (from_source or "").strip()

    async def read_file(self, file_path: str, max_chars: Optional[int] = None,
                        user_id: str = "", session_id: str = "") -> str:
        body: Dict[str, Any] = {"file": file_path}
        if max_chars is not None:
            body["max_chars"] = max_chars
        data = await self._post("/v1/sandbox/file/read", body, user_id, session_id)
        return str(data.get("content", "")) if isinstance(data, dict) else ""

    async def write_file(self, file_path: str, content: str,
                         user_id: str = "", session_id: str = "") -> Dict[str, Any]:
        body = {"file": file_path, "content": content}
        return await self._post("/v1/sandbox/file/write", body, user_id, session_id)

    async def list_directory(self, path: str, recursive: bool = False,
                             user_id: str = "", session_id: str = "") -> list:
        body = {"path": path, "recursive": recursive}
        data = await self._post("/v1/sandbox/file/list", body, user_id, session_id)
        return list(data.get("files", [])) if isinstance(data, dict) else []

    async def grep_files(self, path: str, pattern: str, recursive: bool = True,
                         ignore_case: bool = False,
                         user_id: str = "", session_id: str = "") -> list:
        body = {"path": path, "pattern": pattern, "recursive": recursive, "ignore_case": ignore_case}
        data = await self._post("/v1/sandbox/file/grep", body, user_id, session_id)
        return list(data.get("matches", [])) if isinstance(data, dict) else []

    async def replace_in_file(self, file_path: str, old_str: str, new_str: str,
                              user_id: str = "", session_id: str = "") -> Dict[str, Any]:
        body = {"file": file_path, "old_str": old_str, "new_str": new_str}
        return await self._post("/v1/sandbox/file/replace", body, user_id, session_id)

    async def shell_exec(self, command: str, exec_dir: str = "/workspace",
                         timeout_ms: int = 30000,
                         user_id: str = "", session_id: str = "") -> Dict[str, Any]:
        body = {"command": command, "exec_dir": exec_dir, "timeout_ms": timeout_ms}
        return await self._post("/v1/sandbox/shell/exec", body, user_id, session_id)

    async def _post(self, path: str, body: Dict[str, Any],
                    user_id: str = "", session_id: str = "") -> Any:
        url = f"{self._base_url}{path}"
        headers: Dict[str, str] = {"Content-Type": "application/json; charset=utf-8"}
        if self._from_source:
            headers["X-From-Source"] = self._from_source
        if user_id:
            headers["X-User-Id"] = user_id
        if session_id:
            headers["X-Session-Id"] = session_id
        try:
            raw = await asyncio.to_thread(self._do_post, url, headers, body)
        except Exception as e:
            log_error("Sandbox 服务调用失败", e, url=url)
            raise RuntimeError(f"sandbox-service request failed: {e}")
        payload = json.loads(raw)
        if isinstance(payload, dict) and "error" in payload:
            raise RuntimeError(payload["error"])
        return payload if isinstance(payload, dict) else {}

    def _do_post(self, url: str, headers: Dict[str, str], body: Dict[str, Any]) -> str:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        req = Request(url=url, method="POST", data=data, headers=headers)
        with urlopen(req, timeout=getattr(settings, "SANDBOX_TIMEOUT_SECONDS", 30)) as resp:
            return resp.read().decode("utf-8", errors="replace")
