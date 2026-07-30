from __future__ import annotations

from typing import Annotated, Any

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import Field

from sandbox.application.services.sandbox_session import SandboxSessionService


def build_sandbox_mcp(session: SandboxSessionService) -> FastMCP:
    # 工具层保持无状态；真实租约复用、身份绑定和 fencing 校验都在 SandboxSessionService。
    mcp = FastMCP(
        "wisepen-sandbox-service",
        stateless_http=True,
        json_response=True,
        streamable_http_path="/",
        transport_security=TransportSecuritySettings(
            # 服务挂在内部网关后，由网关负责来源校验；这里关闭 SDK 的 DNS rebinding 检查。
            enable_dns_rebinding_protection=False
        ),
    )

    @mcp.tool(
        name="acquire_sandbox",
        description="Acquire the current user's sandbox lease for this request.",
    )
    async def acquire_sandbox() -> dict[str, Any]:
        lease = await session.acquire()
        return {
            "lease_id": lease.lease_id,
            "request_id": lease.request_id,
            "sandbox_id": lease.sandbox_id,
            "tenant_id": lease.tenant_id,
            "workspace_id": lease.workspace_id,
            "expires_at": lease.expires_at.isoformat(),
            "fencing_token": lease.fencing_token,
        }

    @mcp.tool(
        name="release_sandbox",
        description="Release the current user's sandbox lease for this request.",
    )
    async def release_sandbox() -> dict[str, str]:
        await session.release()
        return {"status": "released"}

    @mcp.tool(
        name="read_file",
        description="Read a file from the current user's sandbox workspace.",
    )
    async def read_file(
        file: Annotated[str, Field(description="Workspace-relative file path.")],
        max_chars: Annotated[int | None, Field(description="Optional output limit.")] = None,
    ) -> dict[str, Any]:
        return await session.execute("read_file", {"file": file, "max_chars": max_chars})

    @mcp.tool(
        name="write_file",
        description="Write a file in the current user's sandbox workspace.",
    )
    async def write_file(
        file: Annotated[str, Field(description="Workspace-relative file path.")],
        content: Annotated[str, Field(description="File content.")],
    ) -> dict[str, Any]:
        return await session.execute("write_file", {"file": file, "content": content})

    @mcp.tool(
        name="list_directory",
        description="List files in the current user's sandbox workspace.",
    )
    async def list_directory(
        path: Annotated[str, Field(description="Workspace-relative directory path.")],
        recursive: Annotated[bool, Field(description="Whether to recurse.")] = False,
    ) -> dict[str, Any]:
        return await session.execute("list_directory", {"path": path, "recursive": recursive})

    @mcp.tool(
        name="grep_files",
        description="Search files in the current user's sandbox workspace.",
    )
    async def grep_files(
        path: Annotated[str, Field(description="Workspace-relative search root.")],
        pattern: Annotated[str, Field(description="Search pattern.")],
        recursive: Annotated[bool, Field(description="Whether to recurse.")] = True,
        ignore_case: Annotated[bool, Field(description="Whether to ignore case.")] = False,
    ) -> dict[str, Any]:
        return await session.execute(
            "grep_files",
            {
                "path": path,
                "pattern": pattern,
                "recursive": recursive,
                "ignore_case": ignore_case,
            },
        )

    @mcp.tool(
        name="edit_file",
        description="Replace one exact string in a workspace file.",
    )
    async def edit_file(
        file: Annotated[str, Field(description="Workspace-relative file path.")],
        old_str: Annotated[str, Field(description="Exact text to replace.")],
        new_str: Annotated[str, Field(description="Replacement text.")],
    ) -> dict[str, Any]:
        return await session.execute(
            "edit_file", {"file": file, "old_str": old_str, "new_str": new_str}
        )

    @mcp.tool(
        name="shell_exec",
        description="Execute a shell command in the current user's sandbox.",
    )
    async def shell_exec(
        command: Annotated[str, Field(description="Shell command.")],
        exec_dir: Annotated[str, Field(description="Workspace-relative working directory.")] = ".",
        timeout_ms: Annotated[int, Field(description="Execution timeout in milliseconds.")] = 30000,
    ) -> dict[str, Any]:
        return await session.execute(
            "shell_exec",
            {"command": command, "exec_dir": exec_dir, "timeout_ms": timeout_ms},
        )

    @mcp.tool(
        name="run_sandbox_script",
        description="Run source code in the current user's sandbox.",
    )
    async def run_sandbox_script(
        language: Annotated[str, Field(description="Programming language, for example python.")],
        code: Annotated[str, Field(description="Source code to execute.")],
        timeout_ms: Annotated[int | None, Field(description="Optional execution timeout in milliseconds.")] = None,
        limits: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        # 统一代码执行契约，Provider 再负责适配 AIO 的 /v1/code/execute。
        return await session.execute(
            "execute",
            {
                "language": language,
                "code": code,
                "timeout_ms": timeout_ms,
                "limits": limits or {},
            },
        )

    return mcp
