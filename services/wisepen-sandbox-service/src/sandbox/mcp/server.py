"""Sandbox MCP server — exposes 6 file/shell tools via MCP protocol."""
from __future__ import annotations

import asyncio
import json
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from common.logger import error as log_error
from sandbox.gateway.isolation import PathValidationError
from sandbox.gateway.container_utils import execute_on_container
from sandbox.mcp.context import extract_tenant, build_translator

_STREAMABLE_HTTP_PATH = "/"
_MCP_SERVER_NAME = "wisepen-sandbox-mcp-service"


def _scrub_result(result: Any, translator) -> Any:
    """递归替换响应中的物理路径为虚拟路径。"""
    if isinstance(result, dict):
        scrubbed = {}
        for k, v in result.items():
            if k in ("file", "path") and isinstance(v, str):
                scrubbed[k] = translator.reverse(v)
            elif isinstance(v, str) and translator.physical_root in v:
                scrubbed[k] = v.replace(translator.physical_root, "/workspace")
            elif isinstance(v, list):
                scrubbed[k] = [_scrub_result(item, translator) for item in v]
            elif isinstance(v, dict):
                scrubbed[k] = _scrub_result(v, translator)
            else:
                scrubbed[k] = v
        return scrubbed
    return result


async def _run_on_container(
    session_pool,
    uid: str, sid: str,
    method: str, path: str, body: dict,
    executor: Any = None,
) -> dict:
    cid, token = await asyncio.to_thread(session_pool.acquire, uid, sid)
    try:
        if executor is not None:
            return await executor(cid, method, path, body)
        return await execute_on_container(cid, method, path, body)
    finally:
        session_pool.heartbeat(uid, sid)


def build_sandbox_mcp(
    session_pool,
    executor: Any = None,
) -> FastMCP:
    """Build the MCP server. session_pool handles container acquire + heartbeat.
    Pass `executor` for dev mode (MockSandbox.execute)."""
    from functools import partial

    _run = partial(_run_on_container, session_pool, executor=executor)

    mcp = FastMCP(
        _MCP_SERVER_NAME,
        stateless_http=True,
        json_response=True,
        streamable_http_path=_STREAMABLE_HTTP_PATH,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=False,
        ),
    )

    @mcp.tool(
        name="read_file",
        description="Read the contents of a file from your sandbox workspace. Use absolute paths under /workspace/ (e.g. /workspace/main.py).",
    )
    async def read_file(file: str, max_chars: int | None = None) -> str:
        uid, sid = extract_tenant()
        if not uid or not sid:
            return json.dumps({"error": "missing X-User-Id or X-Session-Id"})
        try:
            translator = build_translator(uid, sid)
            physical = translator.translate(file)
        except PathValidationError as e:
            return json.dumps({"error": str(e)})
        body: dict[str, Any] = {"file": physical}
        if max_chars is not None:
            body["max_chars"] = max_chars
        try:
            result = await _run(uid, sid, "POST", "/v1/file/read", body)
            return json.dumps(_scrub_result(result, translator))
        except Exception as e:
            log_error("mcp read_file failed", exc=e, file=file)
            return json.dumps({"error": f"read_file failed: {e}"})

    @mcp.tool(
        name="write_file",
        description="Write content to a file in your sandbox workspace under /workspace/.",
    )
    async def write_file(file: str, content: str) -> str:
        uid, sid = extract_tenant()
        if not uid or not sid:
            return json.dumps({"error": "missing X-User-Id or X-Session-Id"})
        try:
            translator = build_translator(uid, sid)
            physical = translator.translate(file)
        except PathValidationError as e:
            return json.dumps({"error": str(e)})
        body = {"file": physical, "content": content, "encoding": "utf-8"}
        try:
            result = await _run(uid, sid, "POST", "/v1/file/write", body)
            return json.dumps(_scrub_result(result, translator))
        except Exception as e:
            log_error("mcp write_file failed", exc=e, file=file)
            return json.dumps({"error": f"write_file failed: {e}"})

    @mcp.tool(
        name="list_directory",
        description="List files and directories under /workspace/.",
    )
    async def list_directory(path: str, recursive: bool = False) -> str:
        uid, sid = extract_tenant()
        if not uid or not sid:
            return json.dumps({"error": "missing X-User-Id or X-Session-Id"})
        try:
            translator = build_translator(uid, sid)
            physical = translator.translate(path)
        except PathValidationError as e:
            return json.dumps({"error": str(e)})
        body = {"path": physical, "recursive": recursive}
        try:
            result = await _run(uid, sid, "POST", "/v1/file/list", body)
            return json.dumps(_scrub_result(result, translator))
        except Exception as e:
            log_error("mcp list_directory failed", exc=e, path=path)
            return json.dumps({"error": f"list_directory failed: {e}"})

    @mcp.tool(
        name="grep_files",
        description="Search for a regex pattern in files under /workspace/. Returns matching lines with file and line number.",
    )
    async def grep_files(
        path: str, pattern: str,
        recursive: bool = True, ignore_case: bool = False,
    ) -> str:
        uid, sid = extract_tenant()
        if not uid or not sid:
            return json.dumps({"error": "missing X-User-Id or X-Session-Id"})
        try:
            translator = build_translator(uid, sid)
            physical = translator.translate(path)
        except PathValidationError as e:
            return json.dumps({"error": str(e)})
        body = {"path": physical, "pattern": pattern, "recursive": recursive, "ignore_case": ignore_case}
        try:
            result = await _run(uid, sid, "POST", "/v1/file/grep", body)
            return json.dumps(_scrub_result(result, translator))
        except Exception as e:
            log_error("mcp grep_files failed", exc=e, path=path, pattern=pattern)
            return json.dumps({"error": f"grep_files failed: {e}"})

    @mcp.tool(
        name="edit_file",
        description="Exact string replacement in a file under /workspace/. old_str must match exactly once (use read_file first to copy exact text).",
    )
    async def edit_file(file: str, old_str: str, new_str: str) -> str:
        uid, sid = extract_tenant()
        if not uid or not sid:
            return json.dumps({"error": "missing X-User-Id or X-Session-Id"})
        try:
            translator = build_translator(uid, sid)
            physical = translator.translate(file)
        except PathValidationError as e:
            return json.dumps({"error": str(e)})
        body = {"file": physical, "old_str": old_str, "new_str": new_str}
        try:
            result = await _run(uid, sid, "POST", "/v1/file/replace", body)
            return json.dumps(_scrub_result(result, translator))
        except Exception as e:
            log_error("mcp edit_file failed", exc=e, file=file)
            return json.dumps({"error": f"edit_file failed: {e}"})

    @mcp.tool(
        name="shell_exec",
        description="Execute a shell command in the sandbox. Returns stdout, stderr, and exit code. Timeout 30s.",
    )
    async def shell_exec(command: str, exec_dir: str = "/workspace", timeout_ms: int = 30000) -> str:
        uid, sid = extract_tenant()
        if not uid or not sid:
            return json.dumps({"error": "missing X-User-Id or X-Session-Id"})
        try:
            translator = build_translator(uid, sid)
            physical_cwd = translator.translate(exec_dir)
        except PathValidationError as e:
            return json.dumps({"error": str(e)})
        body: dict[str, Any] = {"command": command, "exec_dir": physical_cwd}
        if timeout_ms:
            body["timeout"] = timeout_ms // 1000
        try:
            result = await _run(uid, sid, "POST", "/v1/shell/exec", body)
            return json.dumps(_scrub_result(result, translator))
        except Exception as e:
            log_error("mcp shell_exec failed", exc=e, command=command)
            return json.dumps({"error": f"shell_exec failed: {e}"})

    return mcp
