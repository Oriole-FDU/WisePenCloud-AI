from __future__ import annotations

from typing import Annotated, Any

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import Field

from sandbox.application.services.sandbox_session import SandboxSessionService


def build_sandbox_mcp(session: SandboxSessionService) -> FastMCP:
    mcp = FastMCP(
        "wisepen-sandbox-service",
        stateless_http=True,
        json_response=True,
        streamable_http_path="/",
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=False
        ),
    )

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
        description="Run a script package in the current user's sandbox.",
    )
    async def run_sandbox_script(
        package_id: Annotated[str, Field(description="Script package identifier.")],
        entry: str | None = None,
        args: list[str] | None = None,
        env: dict[str, str] | None = None,
        timeout_ms: int | None = None,
        limits: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return await session.execute(
            "execute",
            {
                "package_id": package_id,
                "entry": entry,
                "args": args or [],
                "env": env or {},
                "timeout_ms": timeout_ms,
                "limits": limits or {},
            },
        )

    return mcp
