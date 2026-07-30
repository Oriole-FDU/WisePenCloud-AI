"""Sandbox MCP server — exposes file/shell tools via MCP protocol."""
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
from sandbox.mcp.tool_base import (
    SandboxToolSpec,
    ToolContext,
    register_sandbox_tool,
    _scrub_result as _scrub,
)

_STREAMABLE_HTTP_PATH = "/"
_MCP_SERVER_NAME = "wisepen-sandbox-mcp-service"


async def _run_on_container(
    session_pool,
    uid: str, sid: str,
    method: str, path: str, body: dict,
    executor: Any = None,
) -> dict:
    conn = await asyncio.to_thread(session_pool.acquire, uid, sid)
    cid = conn.container_id
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
            return json.dumps(_scrub(result, translator))
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
            return json.dumps(_scrub(result, translator))
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
            return json.dumps(_scrub(result, translator))
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
            return json.dumps(_scrub(result, translator))
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
            return json.dumps(_scrub(result, translator))
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
            return json.dumps(_scrub(result, translator))
        except Exception as e:
            log_error("mcp shell_exec failed", exc=e, command=command)
            return json.dumps({"error": f"shell_exec failed: {e}"})

    # ---- SandboxScriptTool: host_cache 模式（无需 acquire 容器） ----

    parse_file_spec = SandboxToolSpec.from_json_schema(
        name="parse_file",
        description=(
            "Read and return the full content of a text file from the sandbox workspace. "
            "Use this to inspect the output of scripts or read configuration files. "
            "For binary files (PDF, Word, Excel), shell_exec should be used instead."
        ),
        properties={
            "file": {
                "type": "string",
                "description": "Absolute path under /workspace/ to the file to parse.",
            },
            "max_chars": {
                "type": "integer",
                "description": "Maximum characters to return. Defaults to 20000.",
                "default": 20000,
            },
        },
        required=["file"],
        mode="host_cache",
    )

    async def _parse_file_handler(ctx: ToolContext, file: str, max_chars: int = 20000) -> dict:
        physical = ctx.translate(file)
        # Dev mode mock: use executor if available
        if ctx.executor is not None:
            result = await ctx.executor("mock-cid", "POST", "/v1/file/read",
                                        {"file": physical, "max_chars": max_chars})
            content = result.get("content", "")
            return {"success": True, "content": content, "path": file, "chars_read": len(content)}

        try:
            with open(physical, "r", encoding="utf-8") as fh:
                content = fh.read(max_chars)
        except FileNotFoundError:
            return {"error": f"file not found: {file}"}
        except IsADirectoryError:
            return {"error": f"path is a directory: {file}"}
        except PermissionError:
            return {"error": f"permission denied: {file}"}
        except UnicodeDecodeError:
            return {"error": f"file is not a UTF-8 text file: {file}"}
        return {"success": True, "content": content, "path": file, "chars_read": len(content)}

    register_sandbox_tool(mcp, parse_file_spec, _parse_file_handler, session_pool, executor)

    return mcp
