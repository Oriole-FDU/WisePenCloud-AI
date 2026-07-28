"""Dev-mode mock components for running the sandbox gateway without Docker.

Usage in main.py lifespan:
    from sandbox.gateway.dev_mode import MockSandbox
    mock = MockSandbox()
    mcp_server = build_sandbox_mcp(mock, mock, executor=mock.execute)

MockSandbox is a duck-type that satisfies both ContainerQueue and FileManager
interfaces, plus provides an async execute() method that simulates AIO container
HTTP responses using an in-memory filesystem.
"""
from __future__ import annotations

import fnmatch
import io
import os
import re
import subprocess
from typing import Any

from common.logger import info


class MockSandbox:
    """In-memory sandbox for local dev without Docker.

    Duck-typed to satisfy:
      - queue.acquire(uid, sid) -> (cid, token)
      - queue.release(cid, token)
      - file_manager.pull(cid, uid, sid) / push(cid, uid, sid)
      - async executor(cid, method, path, body) -> dict (AIO response)
    """

    def __init__(self) -> None:
        self._files: dict[str, str] = {}
        self._next_token = 0

    # ---- ContainerQueue duck-type ----

    def acquire(self, user_id: str, session_id: str) -> tuple[str, int]:
        self._next_token += 1
        return ("mock-container", self._next_token)

    def heartbeat(self, user_id: str = "", session_id: str = "") -> None:
        pass  # Mock mode: session affinity is irrelevant

    def release(self, container_id: str, fencing_token: int = 0) -> None:
        pass

    def ensure_idle_count(self) -> int:
        return 1

    def health_check(self) -> dict:
        return {"total": 1, "idle": 1, "busy": 0, "dirty": 0, "dead": 0}

    # ---- FileManager duck-type ----

    def pull(self, container_id: str, user_id: str, session_id: str) -> None:
        pass  # In-memory: no sync needed

    def push(self, container_id: str, user_id: str, session_id: str) -> None:
        pass

    def checkpoint(self, container_id: str, user_id: str, session_id: str) -> bool:
        return False

    # ---- AIO container executor mock ----

    async def execute(self, cid: str, method: str, path: str,
                      body: dict) -> dict:
        """Simulate AIO container API responses against in-memory filesystem."""
        handler = _ROUTES.get(path)
        if handler:
            return await handler(self, body)
        return {"error": f"unknown endpoint: {path}"}

    # ---- Internal helpers for route handlers ----

    def _read(self, file_path: str, max_chars: int | None = None) -> str:
        content = self._files.get(file_path, "")
        if max_chars and len(content) > max_chars:
            content = content[:max_chars]
        return content

    def _write(self, file_path: str, content: str) -> dict:
        self._files[file_path] = content
        return {"success": True, "path": file_path}

    def _list(self, dir_path: str, recursive: bool = False) -> list[dict]:
        results = []
        dir_prefix = dir_path.rstrip("/") + "/"
        seen_dirs = set()
        for fp in sorted(self._files):
            if not fp.startswith(dir_prefix):
                continue
            rel = fp[len(dir_prefix):]
            if not recursive and "/" in rel:
                # Show immediate children only
                top = rel.split("/")[0]
                dir_key = dir_prefix + top
                if dir_key not in seen_dirs:
                    seen_dirs.add(dir_key)
                    results.append({"name": top, "type": "directory",
                                    "path": dir_key})
                continue
            results.append({"name": rel.split("/")[-1], "type": "file",
                            "path": fp, "size": len(self._files[fp])})
        return results

    def _grep(self, dir_path: str, pattern: str, recursive: bool = False,
              ignore_case: bool = False) -> list[dict]:
        flags = re.IGNORECASE if ignore_case else 0
        try:
            regex = re.compile(pattern, flags)
        except re.error as e:
            return [{"error": f"Invalid regex: {e}"}]
        results = []
        dir_slash = dir_path.rstrip("/") + "/"
        for fp, content in sorted(self._files.items()):
            if not fp.startswith(dir_slash):
                continue
            if not recursive and "/" in fp[len(dir_slash):]:
                continue
            for lineno, line in enumerate(content.splitlines(), 1):
                if regex.search(line):
                    results.append({
                        "file": fp, "line": lineno, "content": line.strip(),
                    })
        return results

    def _replace(self, file_path: str, old_str: str, new_str: str) -> dict:
        content = self._files.get(file_path, "")
        if old_str not in content:
            return {"success": False, "reason": "old_str not found"}
        self._files[file_path] = content.replace(old_str, new_str, 1)
        return {"success": True, "path": file_path}

    def _shell_exec(self, command: str, exec_dir: str = "",
                    timeout_ms: int = 30000) -> dict:
        """Mock shell: handles basic filesystem commands."""
        try:
            output = _mock_command(self, command, exec_dir)
            return {"exit_code": 0, "stdout": output, "stderr": ""}
        except Exception as e:
            return {"exit_code": 1, "stdout": "", "stderr": str(e)}


# ---- Route handlers ----

async def _handle_file_read(mock: MockSandbox, body: dict) -> dict:
    max_chars = body.get("max_chars") or None
    content = mock._read(body["file"], max_chars)
    return {"success": True, "content": content}


async def _handle_file_write(mock: MockSandbox, body: dict) -> dict:
    return mock._write(body["file"], body["content"])


async def _handle_file_list(mock: MockSandbox, body: dict) -> dict:
    files = mock._list(body["path"], body.get("recursive", False))
    return {"success": True, "files": files}


async def _handle_file_grep(mock: MockSandbox, body: dict) -> dict:
    matches = mock._grep(
        body["path"], body["pattern"],
        body.get("recursive", False),
        body.get("ignore_case", False),
    )
    return {"success": True, "matches": matches}


async def _handle_file_replace(mock: MockSandbox, body: dict) -> dict:
    return mock._replace(body["file"], body["old_str"], body["new_str"])


async def _handle_shell_exec(mock: MockSandbox, body: dict) -> dict:
    return mock._shell_exec(
        body["command"],
        body.get("exec_dir", ""),
        body.get("timeout_ms", 30000),
    )


_ROUTES: dict[str, Any] = {
    "/v1/file/read": _handle_file_read,
    "/v1/file/write": _handle_file_write,
    "/v1/file/list": _handle_file_list,
    "/v1/file/grep": _handle_file_grep,
    "/v1/file/replace": _handle_file_replace,
    "/v1/shell/exec": _handle_shell_exec,
}


# ---- Simple in-process shell mock ----

def _mock_command(mock: MockSandbox, command: str, exec_dir: str) -> str:
    """Handle basic shell commands against in-memory filesystem."""
    parts = command.strip().split()
    if not parts:
        return ""

    cmd = parts[0]
    args = parts[1:]

    if cmd == "ls":
        target = args[0] if args else (exec_dir or "/workspace")
        files = mock._list(target, recursive=False)
        return "\n".join(f["name"] for f in files)

    if cmd == "cat":
        lines = []
        for fp in args:
            content = mock._read(fp)
            lines.append(content)
        return "\n".join(lines)

    if cmd == "echo":
        return " ".join(args)

    if cmd == "pwd":
        return exec_dir or "/workspace"

    if cmd == "mkdir":
        # Directories are virtual — filesystem is flat, dirs exist if files use them
        return ""

    if cmd in ("touch", "true"):
        return ""

    if cmd in ("python", "python3"):
        # Simple Python evaluation
        script = " ".join(args)
        if script.startswith("-c "):
            script = script[3:]
        return _eval_python(script)

    return f"mock: command '{cmd}' not implemented in dev mode"


def _eval_python(code: str) -> str:
    """Safely evaluate simple Python expressions."""
    try:
        result = eval(code, {"__builtins__": {
            "print": lambda *a, **kw: None,
            "range": range, "len": len, "str": str, "int": int,
            "float": float, "list": list, "dict": dict, "bool": bool,
            "sum": sum, "min": min, "max": max, "sorted": sorted,
            "abs": abs, "round": round, "type": type,
        }})
        return str(result)
    except Exception as e:
        return f"mock python error: {e}"


def docker_available() -> bool:
    """Check if Docker daemon is accessible and responsive."""
    try:
        result = subprocess.run(
            ["docker", "info"], capture_output=True, timeout=5,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def build_mock_sandbox() -> MockSandbox:
    info("Docker not available — starting in mock mode (in-memory filesystem).")
    return MockSandbox()
