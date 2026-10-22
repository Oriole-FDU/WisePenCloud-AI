from __future__ import annotations

from collections.abc import Mapping
from typing import Annotated

from mcp.server.fastmcp import Context, FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import Field, TypeAdapter

from sandbox.application.command_task_service import CommandTaskService
from sandbox.application.file_service import (
    AttachmentImportResult,
    CloudResourceUploadResult,
    ObjectStorageDownloadResult,
    SandboxFileService,
    TemporaryAttachment,
)
from sandbox.domain.entities import CommandTaskDocument
from sandbox.domain.interfaces import (
    FileContentEncoding,
    FileFindResult,
    FileGlobResult,
    FileGlobSortField,
    FileGrepResult,
    FileListResult,
    FileListSortField,
    FileReadResult,
    FileReplaceResult,
    FileSearchResult,
    FileWriteResult,
)

MAX_COMMAND_TIMEOUT_SECONDS = 600
MAX_PATH_LENGTH = 1024
MAX_WRITE_CONTENT_CHARS = 1_000_000
WISEPEN_TOOL_CONFIG_META_KEY = "com.wisepen/tool_config"
WISEPEN_TOOL_CONTEXT_META_KEY = "com.wisepen/tool_context"

def get_tool_config_value(ctx: Context, key: str) -> object | None:
    meta = ctx.request_context.meta
    if not meta:
        return None
    tool_config = (meta.model_extra or {}).get(WISEPEN_TOOL_CONFIG_META_KEY)
    return tool_config.get(key) if isinstance(tool_config, Mapping) else None


def get_tool_context_value(ctx: Context, key: str) -> object | None:
    meta = ctx.request_context.meta
    if not meta:
        return None
    tool_context = (meta.model_extra or {}).get(WISEPEN_TOOL_CONTEXT_META_KEY)
    return tool_context.get(key) if isinstance(tool_context, Mapping) else None


def _result(task: CommandTaskDocument) -> dict[str, str | int | bool | None]:
    return {
        "task_id": task.task_id,
        "status": task.status.value,
        "stdout": task.stdout,
        "stderr": task.stderr,
        "output_truncated": task.output_truncated,
        "exit_code": task.exit_code,
        "error": task.error,
    }


def create_sandbox_mcp_server(
    task_service: CommandTaskService,
    file_service: SandboxFileService | None = None,
) -> FastMCP:
    mcp = FastMCP(
        "wisepen-sandbox-service",
        stateless_http=True,
        json_response=True,
        streamable_http_path="/",
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )

    @mcp.tool(name="start_command", description="Start a shell command in a user's session workspace and return its task ID without waiting.")
    async def start_command(
        ctx: Context,
        command: Annotated[str, Field(min_length=1, description="Shell command to execute.")],
        timeout_seconds: Annotated[int, Field(ge=1, le=MAX_COMMAND_TIMEOUT_SECONDS)] = 60,
    ) -> dict[str, str]:
        task = await task_service.start_command(
            user_id=str(get_tool_context_value(ctx, "user_id")),
            session_id=str(get_tool_context_value(ctx, "session_id")),
            command=command,
            timeout_seconds=timeout_seconds,
        )
        return {"task_id": task.task_id, "status": task.status.value}

    @mcp.tool(name="get_command_result", description="Get a command task result, optionally waiting for completion.")
    async def get_command_result(
        task_id: Annotated[str, Field(min_length=1, description="Random command task ID.")],
        wait: bool = False,
        wait_timeout_seconds: Annotated[float, Field(ge=0, le=MAX_COMMAND_TIMEOUT_SECONDS)] = 0,
    ) -> dict[str, str | int | bool | None]:
        task = await task_service.get_command_result(task_id, wait=wait, wait_timeout_seconds=wait_timeout_seconds)
        return _result(task)

    @mcp.tool(name="kill_command", description="Request termination of a running command task. A successful response means AIO accepted the request; it does not wait for remote process confirmation.")
    async def kill_command(
        task_id: Annotated[str, Field(min_length=1, description="Random command task ID.")],
    ) -> dict[str, str]:
        task = await task_service.kill_command(task_id)
        return {"task_id": task.task_id, "status": task.status.value}

    @mcp.tool(name="execute_command", description="Execute a shell command in a user's session workspace and wait for its final result.")
    async def execute_command(
        ctx: Context,
        command: Annotated[str, Field(min_length=1, description="Shell command to execute.")],
        timeout_seconds: Annotated[int, Field(ge=1, le=MAX_COMMAND_TIMEOUT_SECONDS)] = 60,
    ) -> dict[str, str | int | bool | None]:
        task = await task_service.execute_command(
            user_id=str(get_tool_context_value(ctx, "user_id")),
            session_id=str(get_tool_context_value(ctx, "session_id")),
            command=command,
            timeout_seconds=timeout_seconds,
        )
        return _result(task)

    if file_service is None:
        return mcp

    @mcp.tool(name="read_file", description="Read file content from a workspace-relative path. Line indexes are zero-based and end_line is exclusive.")
    async def read_file(
        ctx: Context,
        file: Annotated[str, Field(min_length=1, max_length=MAX_PATH_LENGTH, description="Workspace-relative file path.")],
        start_line: int | None = None,
        end_line: int | None = None,
    ) -> FileReadResult:
        return await file_service.read_file(
            user_id=str(get_tool_context_value(ctx, "user_id")),
            session_id=str(get_tool_context_value(ctx, "session_id")),
            path=file,
            start_line=start_line, end_line=end_line,
        )

    @mcp.tool(name="write_file", description="Write file content. Use base64 encoding for binary content and upload_file for user attachments.")
    async def write_file(
        ctx: Context,
        file: Annotated[str, Field(min_length=1, max_length=MAX_PATH_LENGTH, description="Workspace-relative file path.")],
        content: Annotated[str, Field(max_length=MAX_WRITE_CONTENT_CHARS)],
        encoding: FileContentEncoding = FileContentEncoding.UTF8,
        append: bool = False,
        leading_newline: bool = False,
        trailing_newline: bool = False,
    ) -> FileWriteResult:
        return await file_service.write_file(
            user_id=str(get_tool_context_value(ctx, "user_id")),
            session_id=str(get_tool_context_value(ctx, "session_id")),
            path=file, content=content,
            encoding=encoding, append=append,
            leading_newline=leading_newline,
            trailing_newline=trailing_newline,
        )

    @mcp.tool(name="replace_in_file", description="Replace an exact string in a file.")
    async def replace_in_file(
        ctx: Context,
        file: Annotated[str, Field(min_length=1, max_length=MAX_PATH_LENGTH, description="Workspace-relative file path.")],
        old_str: Annotated[str, Field(min_length=1)],
        new_str: str,
    ) -> FileReplaceResult:
        return await file_service.replace_in_file(
            user_id=str(get_tool_context_value(ctx, "user_id")),
            session_id=str(get_tool_context_value(ctx, "session_id")),
            path=file,
            old_str=old_str, new_str=new_str,
        )

    @mcp.tool(name="search_in_file", description="Search file content with a regular expression.")
    async def search_in_file(
        ctx: Context,
        file: Annotated[str, Field(min_length=1, max_length=MAX_PATH_LENGTH, description="Workspace-relative file path.")],
        regex: Annotated[str, Field(min_length=1)],
    ) -> FileSearchResult:
        return await file_service.search_in_file(
            user_id=str(get_tool_context_value(ctx, "user_id")),
            session_id=str(get_tool_context_value(ctx, "session_id")),
            path=file,
            regex=regex,
        )

    @mcp.tool(name="find_files", description="Find files by a filename glob.")
    async def find_files(
        ctx: Context,
        path: Annotated[str, Field(min_length=1, max_length=MAX_PATH_LENGTH, description="Workspace-relative directory path.")],
        glob: Annotated[str, Field(min_length=1, description="Filename glob such as *.py.")],
    ) -> FileFindResult:
        return await file_service.find_files(
            user_id=str(get_tool_context_value(ctx, "user_id")),
            session_id=str(get_tool_context_value(ctx, "session_id")),
            path=path,
            glob=glob,
        )

    @mcp.tool(name="grep_files", description="Search file contents with regex or fixed-string matching.")
    async def grep_files(
        ctx: Context,
        path: Annotated[str, Field(min_length=1, max_length=MAX_PATH_LENGTH, description="Workspace-relative file or directory path.")],
        pattern: Annotated[str, Field(min_length=1)],
        include: list[str] | None = None,
        exclude: list[str] | None = None,
        case_insensitive: bool = False,
        fixed_strings: bool = False,
        context_before: Annotated[int, Field(ge=0, le=20)] = 0,
        context_after: Annotated[int, Field(ge=0, le=20)] = 0,
        max_results: Annotated[int, Field(ge=1, le=10000)] = 500,
        max_file_size: str = "1M",
        multiline: bool = False,
        offset: Annotated[int, Field(ge=0)] = 0,
        type: str | None = None,
        recursive: bool = True,
    ) -> FileGrepResult:
        return await file_service.grep_files(
            user_id=str(get_tool_context_value(ctx, "user_id")),
            session_id=str(get_tool_context_value(ctx, "session_id")),
            path=path, pattern=pattern,
            include=tuple(include) if include is not None else None,
            exclude=tuple(exclude) if exclude is not None else None,
            case_insensitive=case_insensitive, fixed_strings=fixed_strings,
            context_before=context_before, context_after=context_after,
            max_results=max_results, max_file_size=max_file_size,
            multiline=multiline, offset=offset, file_type=type, recursive=recursive,
        )

    @mcp.tool(name="glob_files", description="Match files using an advanced glob with optional metadata and sorting.")
    async def glob_files(
        ctx: Context,
        path: Annotated[str, Field(min_length=1, max_length=MAX_PATH_LENGTH, description="Workspace-relative base directory.")],
        pattern: Annotated[str, Field(min_length=1)],
        exclude: list[str] | None = None,
        include_hidden: bool = False,
        files_only: bool = True,
        include_metadata: bool = True,
        max_results: Annotated[int, Field(ge=1, le=50000)] = 5000,
        sort_by: FileGlobSortField = FileGlobSortField.PATH,
        sort_desc: bool = False,
    ) -> FileGlobResult:
        return await file_service.glob_files(
            user_id=str(get_tool_context_value(ctx, "user_id")),
            session_id=str(get_tool_context_value(ctx, "session_id")),
            path=path, pattern=pattern,
            exclude=tuple(exclude) if exclude is not None else None,
            include_hidden=include_hidden, files_only=files_only,
            include_metadata=include_metadata, max_results=max_results,
            sort_by=sort_by, sort_desc=sort_desc,
        )

    @mcp.tool(name="list_files", description="List files and directories with optional recursion, metadata and sorting.")
    async def list_files(
        ctx: Context,
        path: Annotated[str, Field(min_length=1, max_length=MAX_PATH_LENGTH, description="Workspace-relative directory path.")],
        recursive: bool = False,
        show_hidden: bool = True,
        file_types: list[str] | None = None,
        max_depth: int | None = None,
        include_size: bool = True,
        include_permissions: bool = False,
        sort_by: FileListSortField = FileListSortField.NAME,
        sort_desc: bool = False,
    ) -> FileListResult:
        return await file_service.list_files(
            user_id=str(get_tool_context_value(ctx, "user_id")),
            session_id=str(get_tool_context_value(ctx, "session_id")),
            path=path, recursive=recursive,
            show_hidden=show_hidden,
            file_types=tuple(file_types) if file_types is not None else None,
            max_depth=max_depth, include_size=include_size,
            include_permissions=include_permissions, sort_by=sort_by,
            sort_desc=sort_desc,
        )

    @mcp.tool(name="upload_file", description="Upload a current-session attachment into the sandbox workspace. The source is an attachment_id; arbitrary URLs are not accepted.")
    async def upload_file(
        ctx: Context,
        attachment_id: Annotated[str, Field(min_length=1, description="Attachment ID from the current chat session.")],
        destination_path: Annotated[str, Field(min_length=1, max_length=MAX_PATH_LENGTH, description="Workspace-relative destination path.")],
    ) -> AttachmentImportResult:
        attachments = TypeAdapter(list[TemporaryAttachment]).validate_python(
            get_tool_context_value(ctx, "temporary_attachment_refs") or []
        )
        return await file_service.upload_file(
            user_id=str(get_tool_context_value(ctx, "user_id")),
            session_id=str(get_tool_context_value(ctx, "session_id")),
            attachment_id=attachment_id,
            destination_path=destination_path, temporary_attachment_refs=attachments,
        )

    @mcp.tool(name="download_file", description="Download a sandbox file through private object storage and return a temporary OSS URL. This does not create a chat attachment.")
    async def download_file(
        ctx: Context,
        path: Annotated[str, Field(min_length=1, max_length=MAX_PATH_LENGTH, description="Workspace-relative file path.")],
    ) -> ObjectStorageDownloadResult:
        return await file_service.download_file(
            user_id=str(get_tool_context_value(ctx, "user_id")),
            session_id=str(get_tool_context_value(ctx, "session_id")),
            path=path,
        )

    @mcp.tool(
        name="upload_file_to_cloud_drive",
        description=(
            "Upload a supported document or HTML file from the current sandbox workspace "
            "as a new cloud-drive resource. Supports PDF, DOC, DOCX, PPT, PPTX, XLS, XLSX, "
            "HTML, and HTM files. A successful result means the upload was submitted; "
            "resource processing may continue asynchronously."
        ),
    )
    async def upload_file_to_cloud_drive(
        ctx: Context,
        path: Annotated[
            str,
            Field(
                min_length=1,
                max_length=MAX_PATH_LENGTH,
                description="Workspace-relative path of the file to upload as a new cloud-drive resource.",
            ),
        ],
    ) -> CloudResourceUploadResult:
        return await file_service.upload_file_to_cloud_drive(
            user_id=str(get_tool_context_value(ctx, "user_id")),
            session_id=str(get_tool_context_value(ctx, "session_id")),
            path=path,
        )

    return mcp
