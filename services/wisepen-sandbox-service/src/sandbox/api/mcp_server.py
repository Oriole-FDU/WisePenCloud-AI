from __future__ import annotations

from base64 import b64encode
from collections.abc import Mapping
from typing import Annotated, Literal, Union

from mcp.server.fastmcp import Context, FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ImageContent, TextContent
from pydantic import BaseModel, Field, TypeAdapter

from common.mcp import (
    OutputCacheSpecMeta,
    OutputCacheStrategy,
    TOOL_CONFIG_META_KEY,
    TOOL_CONTEXT_META_KEY,
    ToolMeta,
    ToolPolicyMeta,
    ToolRiskLevel,
    ToolUISpecMeta,
    wisepen_mcp_meta,
)
from sandbox.application.command_task_service import CommandTaskService
from sandbox.application.browser_service import SandboxBrowserService
from sandbox.application.file_service import (
    AttachmentImportResult,
    CloudResourceUploadResult,
    ObjectStorageDownloadResult,
    SandboxFileService,
    TemporaryAttachment,
)
from sandbox.domain.entities import CommandTaskDocument
from sandbox.domain.interfaces import (
    BrowserActionResult,
    BrowserContentResult,
    BrowserListResult,
    BrowserViewport,
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
MAX_PYTHON_SOURCE_CHARS = 16_384
MAX_PATH_LENGTH = 1024
MAX_WRITE_CONTENT_CHARS = 1_000_000

_OPAQUE_OUTPUT_CACHE = OutputCacheSpecMeta(strategy=OutputCacheStrategy.OPAQUE)
_ITEMS_FILE_CONTENT_CACHE = OutputCacheSpecMeta(strategy=OutputCacheStrategy.ITEMS, cache_paths=("content",))
_ITEMS_COMMAND_OUTPUT_CACHE = OutputCacheSpecMeta(strategy=OutputCacheStrategy.ITEMS, cache_paths=("stdout", "stderr"))


def get_tool_config_value(ctx: Context, key: str) -> object | None:
    meta = ctx.request_context.meta
    if not meta:
        return None
    tool_config = (meta.model_extra or {}).get(TOOL_CONFIG_META_KEY)
    return tool_config.get(key) if isinstance(tool_config, Mapping) else None


def get_tool_context_value(ctx: Context, key: str) -> object | None:
    meta = ctx.request_context.meta
    if not meta:
        return None
    tool_context = (meta.model_extra or {}).get(TOOL_CONTEXT_META_KEY)
    return tool_context.get(key) if isinstance(tool_context, Mapping) else None


def _session_context(ctx: Context) -> tuple[str, str]:
    """从 MCP 请求上下文提取 user_id 与 session_id。"""
    return (
        str(get_tool_context_value(ctx, "user_id")),
        str(get_tool_context_value(ctx, "session_id")),
    )


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


class MoveToAction(BaseModel):
    """移动鼠标到屏幕绝对坐标。"""

    action_type: Literal["MOVE_TO"] = "MOVE_TO"
    x: float = Field(description="Target x-coordinate")
    y: float = Field(description="Target y-coordinate")


class MoveRelAction(BaseModel):
    """相对当前鼠标位置移动。"""

    action_type: Literal["MOVE_REL"] = "MOVE_REL"
    x_offset: float = Field(description="Relative x-axis movement from current position")
    y_offset: float = Field(description="Relative y-axis movement from current position")


class ClickAction(BaseModel):
    """点击；省略坐标则点击当前鼠标位置。"""

    action_type: Literal["CLICK"] = "CLICK"
    x: float | None = Field(default=None, description="Target x-coordinate")
    y: float | None = Field(default=None, description="Target y-coordinate")
    button: Literal["left", "right", "middle"] = "left"
    num_clicks: Literal[1, 2, 3] = 1


class MouseDownAction(BaseModel):
    """按下鼠标按键。"""

    action_type: Literal["MOUSE_DOWN"] = "MOUSE_DOWN"
    button: Literal["left", "right", "middle"] = "left"


class MouseUpAction(BaseModel):
    """释放鼠标按键。"""

    action_type: Literal["MOUSE_UP"] = "MOUSE_UP"
    button: Literal["left", "right", "middle"] = "left"


class RightClickAction(BaseModel):
    """右键点击；省略坐标则点击当前鼠标位置。"""

    action_type: Literal["RIGHT_CLICK"] = "RIGHT_CLICK"
    x: float | None = Field(default=None, description="Target x-coordinate")
    y: float | None = Field(default=None, description="Target y-coordinate")


class DoubleClickAction(BaseModel):
    """双击；省略坐标则双击当前鼠标位置。"""

    action_type: Literal["DOUBLE_CLICK"] = "DOUBLE_CLICK"
    x: float | None = Field(default=None, description="Target x-coordinate")
    y: float | None = Field(default=None, description="Target y-coordinate")


class DragToAction(BaseModel):
    """拖拽到屏幕绝对坐标。"""

    action_type: Literal["DRAG_TO"] = "DRAG_TO"
    x: float = Field(description="Target x-coordinate for drag")
    y: float = Field(description="Target y-coordinate for drag")


class DragRelAction(BaseModel):
    """相对当前鼠标位置拖拽。"""

    action_type: Literal["DRAG_REL"] = "DRAG_REL"
    x_offset: float = Field(description="Relative x-axis drag movement from current position")
    y_offset: float = Field(description="Relative y-axis drag movement from current position")


class ScrollAction(BaseModel):
    """滚动屏幕（像素量）。"""

    action_type: Literal["SCROLL"] = "SCROLL"
    dx: int = Field(default=0, description="Horizontal scroll amount in pixels")
    dy: int = Field(default=0, description="Vertical scroll amount in pixels")


class TypingAction(BaseModel):
    """输入文本。"""

    action_type: Literal["TYPING"] = "TYPING"
    text: str = Field(min_length=1, description="Text to type")
    use_clipboard: bool | None = Field(
        default=True,
        description="Use clipboard for better character support",
    )


class PressAction(BaseModel):
    """按下并释放单个按键。"""

    action_type: Literal["PRESS"] = "PRESS"
    key: str = Field(min_length=1, description="Key name")


class KeyDownAction(BaseModel):
    """按下单个按键（不释放）。"""

    action_type: Literal["KEY_DOWN"] = "KEY_DOWN"
    key: str = Field(min_length=1, description="Key name")


class KeyUpAction(BaseModel):
    """释放单个按键。"""

    action_type: Literal["KEY_UP"] = "KEY_UP"
    key: str = Field(min_length=1, description="Key name")


class HotkeyAction(BaseModel):
    """按下组合键。"""

    action_type: Literal["HOTKEY"] = "HOTKEY"
    keys: list[str] = Field(min_length=1, description="Ordered key names to press together")


class WaitAction(BaseModel):
    """等待指定秒数。"""

    action_type: Literal["WAIT"] = "WAIT"
    duration: float = Field(gt=0, description="Duration to wait in seconds")


BrowserAction = Annotated[
    Union[
        MoveToAction,
        MoveRelAction,
        ClickAction,
        MouseDownAction,
        MouseUpAction,
        RightClickAction,
        DoubleClickAction,
        DragToAction,
        DragRelAction,
        ScrollAction,
        TypingAction,
        PressAction,
        KeyDownAction,
        KeyUpAction,
        HotkeyAction,
        WaitAction,
    ],
    Field(discriminator="action_type"),
]


class BrowserInfoView(BaseModel):
    """browser_get_info 对外精简视图"""

    user_agent: str
    viewport: BrowserViewport
    page_viewport: BrowserViewport | None = None


def create_sandbox_mcp_server(
    task_service: CommandTaskService,
    file_service: SandboxFileService | None = None,
    browser_service: SandboxBrowserService | None = None,
) -> FastMCP:
    mcp = FastMCP(
        "wisepen-sandbox-service",
        stateless_http=True,
        json_response=True,
        streamable_http_path="/",
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )

    @wisepen_mcp_meta(ToolMeta(
        tool_name="start_command",
        policy=ToolPolicyMeta(
            expose_by_default=True,
            risk_level=ToolRiskLevel.MEDIUM,
            timeout_seconds=15.0,
            persist_output=True,
            required_context_keys=("user_id", "session_id"),
        ),
        ui_spec=ToolUISpecMeta(display_name="启动沙箱命令"),
        failure_reason="Sandbox Command Start Failed",
    ))
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

    @wisepen_mcp_meta(ToolMeta(
        tool_name="get_command_result",
        policy=ToolPolicyMeta(
            expose_by_default=True,
            risk_level=ToolRiskLevel.MEDIUM,
            timeout_seconds=620.0,
            persist_output=True,
            required_context_keys=("user_id", "session_id"),
        ),
        output_cache_spec=_ITEMS_COMMAND_OUTPUT_CACHE,
        failure_reason="Sandbox Command Result Read Failed",
    ))
    @mcp.tool(name="get_command_result", description="Get a command task result, optionally waiting for completion.")
    async def get_command_result(
        task_id: Annotated[str, Field(min_length=1, description="Random command task ID.")],
        wait: bool = False,
        wait_timeout_seconds: Annotated[float, Field(ge=0, le=MAX_COMMAND_TIMEOUT_SECONDS)] = 0,
    ) -> dict[str, str | int | bool | None]:
        task = await task_service.get_command_result(task_id, wait=wait, wait_timeout_seconds=wait_timeout_seconds)
        return _result(task)

    @wisepen_mcp_meta(ToolMeta(
        tool_name="kill_command",
        policy=ToolPolicyMeta(
            expose_by_default=True,
            risk_level=ToolRiskLevel.MEDIUM,
            timeout_seconds=15.0,
            persist_output=True,
            required_context_keys=("user_id", "session_id"),
        ),
        ui_spec=ToolUISpecMeta(display_name="终止沙箱命令"),
        failure_reason="Sandbox Command Termination Failed",
    ))
    @mcp.tool(name="kill_command", description="Request termination of a running command task. A successful response means AIO accepted the request; it does not wait for remote process confirmation.")
    async def kill_command(
        task_id: Annotated[str, Field(min_length=1, description="Random command task ID.")],
    ) -> dict[str, str]:
        task = await task_service.kill_command(task_id)
        return {"task_id": task.task_id, "status": task.status.value}

    @wisepen_mcp_meta(ToolMeta(
        tool_name="execute_command",
        policy=ToolPolicyMeta(
            expose_by_default=True,
            risk_level=ToolRiskLevel.MEDIUM,
            timeout_seconds=620.0,
            persist_output=True,
            required_context_keys=("user_id", "session_id"),
        ),
        output_cache_spec=_ITEMS_COMMAND_OUTPUT_CACHE,
        failure_reason="Sandbox Command Execution Failed",
    ))
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

    @wisepen_mcp_meta(ToolMeta(
        tool_name="run_python",
        policy=ToolPolicyMeta(
            expose_by_default=True,
            risk_level=ToolRiskLevel.MEDIUM,
            timeout_seconds=620.0,
            persist_output=True,
            required_context_keys=("user_id", "session_id"),
        ),
        ui_spec=ToolUISpecMeta(display_name="运行沙箱 Python"),
        output_cache_spec=_ITEMS_COMMAND_OUTPUT_CACHE,
        failure_reason="Sandbox Python Execution Failed",
    ))
    @mcp.tool(
        name="run_python",
        description=("Run raw multiline Python in the current session workspace with no interactive input. "
                     "Pass Python directly as source, without Markdown fences or shell quoting."),
    )
    async def run_python(
        ctx: Context,
        source: Annotated[str, Field(min_length=1, max_length=MAX_PYTHON_SOURCE_CHARS,
                                     description="Raw Python source, without Markdown fences.")],
        timeout_seconds: Annotated[int, Field(ge=1, le=MAX_COMMAND_TIMEOUT_SECONDS)] = 30,
    ) -> dict[str, str | int | bool | None]:
        # AIO 的命令接口只接收 Bash 文本，源码编码为受长度限制的 ASCII 数据。
        # 解码后的源码经 stdin 交给 Python，Bash 不解析源码里的引号、换行或变量。
        payload = b64encode(source.encode("utf-8")).decode("ascii")
        task = await task_service.execute_command(
            user_id=str(get_tool_context_value(ctx, "user_id")),
            session_id=str(get_tool_context_value(ctx, "session_id")),
            command=f"printf '%s' '{payload}' | base64 -d | python3 -X utf8 -u -",
            timeout_seconds=timeout_seconds,
        )
        return _result(task)

    if file_service is None:
        return mcp

    @wisepen_mcp_meta(ToolMeta(
        tool_name="read_file",
        policy=ToolPolicyMeta(
            expose_by_default=True,
            risk_level=ToolRiskLevel.LOW,
            timeout_seconds=30.0,
            persist_output=True,
            required_context_keys=("user_id", "session_id"),
        ),
        ui_spec=ToolUISpecMeta(display_name="读取沙箱文件"),
        output_cache_spec=_ITEMS_FILE_CONTENT_CACHE,
        failure_reason="Sandbox File Read Failed",
    ))
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

    @wisepen_mcp_meta(ToolMeta(
        tool_name="view_image",
        policy=ToolPolicyMeta(
            expose_by_default=True,
            risk_level=ToolRiskLevel.LOW,
            timeout_seconds=30.0,
            persist_output=True,
            required_context_keys=("user_id", "session_id"),
        ),
        ui_spec=ToolUISpecMeta(display_name="查看沙箱图片"),
        failure_reason="Sandbox Image Read Failed",
    ))
    @mcp.tool(
        name="view_image",
        description=("View a PNG, JPEG, WebP, GIF, BMP, TIFF, ICO or AVIF in the current sandbox workspace. "
                     "For animated or multi-page images, view the first frame."),
        structured_output=False,
    )
    async def view_image(
        ctx: Context,
        path: Annotated[str, Field(min_length=1, max_length=MAX_PATH_LENGTH,
                                   description="Workspace-relative image path.")],
    ) -> list[TextContent | ImageContent]:
        name, source_format, mime_type, data = await file_service.read_image(
            user_id=str(get_tool_context_value(ctx, "user_id")),
            session_id=str(get_tool_context_value(ctx, "session_id")),
            path=path,
        )
        # 图片作为 MCP 内容块传给模型，文本回执只保留工作区路径。
        return [
            TextContent(type="text", text=f"Image: {name} ({source_format})"),
            ImageContent(type="image", mimeType=mime_type, data=b64encode(data).decode("ascii")),
        ]

    @wisepen_mcp_meta(ToolMeta(
        tool_name="write_file",
        policy=ToolPolicyMeta(
            expose_by_default=True,
            risk_level=ToolRiskLevel.MEDIUM,
            timeout_seconds=30.0,
            persist_output=True,
            required_context_keys=("user_id", "session_id"),
        ),
        ui_spec=ToolUISpecMeta(display_name="写入沙箱文件"),
        failure_reason="Sandbox File Write Failed",
    ))
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

    @wisepen_mcp_meta(ToolMeta(
        tool_name="replace_in_file",
        policy=ToolPolicyMeta(
            expose_by_default=True,
            risk_level=ToolRiskLevel.MEDIUM,
            timeout_seconds=30.0,
            persist_output=True,
            required_context_keys=("user_id", "session_id"),
        ),
        ui_spec=ToolUISpecMeta(display_name="替换沙箱文件内容"),
        failure_reason="Sandbox File Replace Failed",
    ))
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

    @wisepen_mcp_meta(ToolMeta(
        tool_name="search_in_file",
        policy=ToolPolicyMeta(
            expose_by_default=True,
            risk_level=ToolRiskLevel.LOW,
            timeout_seconds=30.0,
            persist_output=True,
            required_context_keys=("user_id", "session_id"),
        ),
        ui_spec=ToolUISpecMeta(display_name="搜索沙箱文件"),
        output_cache_spec=_OPAQUE_OUTPUT_CACHE,
        failure_reason="Sandbox File Search Failed",
    ))
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

    @wisepen_mcp_meta(ToolMeta(
        tool_name="find_files",
        policy=ToolPolicyMeta(
            expose_by_default=True,
            risk_level=ToolRiskLevel.LOW,
            timeout_seconds=30.0,
            persist_output=True,
            required_context_keys=("user_id", "session_id"),
        ),
        ui_spec=ToolUISpecMeta(display_name="查找沙箱文件"),
        output_cache_spec=_OPAQUE_OUTPUT_CACHE,
        failure_reason="Sandbox File Find Failed",
    ))
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

    @wisepen_mcp_meta(ToolMeta(
        tool_name="grep_files",
        policy=ToolPolicyMeta(
            expose_by_default=True,
            risk_level=ToolRiskLevel.LOW,
            timeout_seconds=30.0,
            persist_output=True,
            required_context_keys=("user_id", "session_id"),
        ),
        ui_spec=ToolUISpecMeta(display_name="检索沙箱文件内容"),
        output_cache_spec=_OPAQUE_OUTPUT_CACHE,
        failure_reason="Sandbox File Grep Failed",
    ))
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

    @wisepen_mcp_meta(ToolMeta(
        tool_name="glob_files",
        policy=ToolPolicyMeta(
            expose_by_default=True,
            risk_level=ToolRiskLevel.LOW,
            timeout_seconds=30.0,
            persist_output=True,
            required_context_keys=("user_id", "session_id"),
        ),
        ui_spec=ToolUISpecMeta(display_name="Glob 沙箱文件"),
        output_cache_spec=_OPAQUE_OUTPUT_CACHE,
        failure_reason="Sandbox File Glob Failed",
    ))
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

    @wisepen_mcp_meta(ToolMeta(
        tool_name="list_files",
        policy=ToolPolicyMeta(
            expose_by_default=True,
            risk_level=ToolRiskLevel.LOW,
            timeout_seconds=30.0,
            persist_output=True,
            required_context_keys=("user_id", "session_id"),
        ),
        ui_spec=ToolUISpecMeta(display_name="列出沙箱文件"),
        output_cache_spec=OutputCacheSpecMeta(strategy=OutputCacheStrategy.ALWAYS_KEEP_RAW),
        failure_reason="Sandbox File List Failed",
    ))
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

    @wisepen_mcp_meta(ToolMeta(
        tool_name="upload_file",
        policy=ToolPolicyMeta(
            expose_by_default=True,
            risk_level=ToolRiskLevel.MEDIUM,
            timeout_seconds=150.0,
            persist_output=True,
            required_context_keys=("user_id", "session_id", "temporary_attachment_refs"),
        ),
        ui_spec=ToolUISpecMeta(display_name="导入附件到沙箱"),
        failure_reason="Sandbox Attachment Import Failed",
    ))
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

    @wisepen_mcp_meta(ToolMeta(
        tool_name="download_file",
        policy=ToolPolicyMeta(
            expose_by_default=True,
            risk_level=ToolRiskLevel.MEDIUM,
            timeout_seconds=150.0,
            persist_output=True,
            required_context_keys=("user_id", "session_id"),
        ),
        ui_spec=ToolUISpecMeta(display_name="导出沙箱文件"),
        failure_reason="Sandbox File Export Failed",
    ))
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

    @wisepen_mcp_meta(ToolMeta(
        tool_name="upload_file_to_cloud_drive",
        policy=ToolPolicyMeta(
            expose_by_default=True,
            risk_level=ToolRiskLevel.MEDIUM,
            timeout_seconds=150.0,
            persist_output=True,
            required_context_keys=("user_id", "session_id"),
        ),
        ui_spec=ToolUISpecMeta(
            display_name="上传沙箱文件到云盘",
            description="将沙箱中的文档或 HTML 文件上传为新的云盘资源。",
        ),
        failure_reason="Sandbox Cloud Resource Upload Failed",
    ))
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

    if browser_service is None:
        return mcp

    @wisepen_mcp_meta(ToolMeta(
        tool_name="browser_get_info",
        policy=ToolPolicyMeta(
            expose_by_default=True,
            risk_level=ToolRiskLevel.LOW,
            timeout_seconds=30.0,
            persist_output=True,
            required_context_keys=("user_id", "session_id"),
        ),
        ui_spec=ToolUISpecMeta(display_name="获取浏览器信息"),
        failure_reason="Sandbox Browser Info Read Failed",
    ))
    @mcp.tool(name="browser_get_info", description="Get the browser user agent and viewport size.")
    async def browser_get_info(ctx: Context) -> BrowserInfoView:
        user_id, session_id = _session_context(ctx)
        info = await browser_service.get_info(user_id=user_id, session_id=session_id)
        return BrowserInfoView(
            user_agent=info.user_agent,
            viewport=info.viewport,
            page_viewport=info.page_viewport,
        )

    @wisepen_mcp_meta(ToolMeta(
        tool_name="browser_navigate",
        policy=ToolPolicyMeta(
            expose_by_default=True,
            risk_level=ToolRiskLevel.MEDIUM,
            timeout_seconds=120.0,
            persist_output=True,
            required_context_keys=("user_id", "session_id"),
        ),
        ui_spec=ToolUISpecMeta(display_name="浏览器导航"),
        output_cache_spec=_OPAQUE_OUTPUT_CACHE,
        failure_reason="Sandbox Browser Navigation Failed",
    ))
    @mcp.tool(name="browser_navigate", description="Navigate the current browser page to a URL.")
    async def browser_navigate(
        ctx: Context,
        url: Annotated[str, Field(min_length=1, description="Target URL to open.")],
        wait_until: str | None = None,
        timeout: float | None = None,
    ) -> BrowserActionResult:
        user_id, session_id = _session_context(ctx)
        result = await browser_service.navigate(
            user_id=user_id, session_id=session_id, url=url, wait_until=wait_until, timeout=timeout,
        )
        return result

    @wisepen_mcp_meta(ToolMeta(
        tool_name="browser_snapshot",
        policy=ToolPolicyMeta(
            expose_by_default=True,
            risk_level=ToolRiskLevel.LOW,
            timeout_seconds=60.0,
            persist_output=True,
            required_context_keys=("user_id", "session_id"),
        ),
        ui_spec=ToolUISpecMeta(display_name="读取浏览器页面内容"),
        output_cache_spec=_OPAQUE_OUTPUT_CACHE,
        failure_reason="Sandbox Browser Snapshot Failed",
    ))
    @mcp.tool(name="browser_snapshot", description="Get the current page content as Markdown.")
    async def browser_snapshot(ctx: Context) -> BrowserContentResult:
        user_id, session_id = _session_context(ctx)
        return await browser_service.get_markdown(user_id=user_id, session_id=session_id)

    @wisepen_mcp_meta(ToolMeta(
        tool_name="browser_screenshot",
        policy=ToolPolicyMeta(
            expose_by_default=True,
            risk_level=ToolRiskLevel.LOW,
            timeout_seconds=30.0,
            persist_output=True,
            required_context_keys=("user_id", "session_id"),
        ),
        ui_spec=ToolUISpecMeta(display_name="浏览器截图"),
        failure_reason="Sandbox Browser Screenshot Failed",
    ))
    @mcp.tool(
        name="browser_screenshot",
        description="Take a screenshot of the current browser display.",
        structured_output=False,
    )
    async def browser_screenshot(
        ctx: Context,
        format: str = "png",
        quality: int | None = None,
    ) -> list[TextContent | ImageContent]:
        user_id, session_id = _session_context(ctx)
        mime_type, data = await browser_service.screenshot(
            user_id=user_id, session_id=session_id, format=format, quality=quality,
        )
        return [
            TextContent(type="text", text=f"Browser screenshot ({format})"),
            ImageContent(type="image", mimeType=mime_type, data=b64encode(data).decode("ascii")),
        ]

    @wisepen_mcp_meta(ToolMeta(
        tool_name="browser_click",
        policy=ToolPolicyMeta(
            expose_by_default=True,
            risk_level=ToolRiskLevel.MEDIUM,
            timeout_seconds=60.0,
            persist_output=True,
            required_context_keys=("user_id", "session_id"),
        ),
        ui_spec=ToolUISpecMeta(display_name="浏览器点击"),
        output_cache_spec=_OPAQUE_OUTPUT_CACHE,
        failure_reason="Sandbox Browser Click Failed",
    ))
    @mcp.tool(name="browser_click", description="Click an element by CSS selector, index, or coordinates.")
    async def browser_click(
        ctx: Context,
        selector: str | None = None,
        index: int | None = None,
        x: float | None = None,
        y: float | None = None,
        button: str | None = None,
        click_count: int | None = None,
    ) -> BrowserActionResult:
        user_id, session_id = _session_context(ctx)
        result = await browser_service.click(
            user_id=user_id, session_id=session_id,
            selector=selector, index=index, x=x, y=y, button=button, click_count=click_count,
        )
        return result

    @wisepen_mcp_meta(ToolMeta(
        tool_name="browser_fill",
        policy=ToolPolicyMeta(
            expose_by_default=True,
            risk_level=ToolRiskLevel.MEDIUM,
            timeout_seconds=60.0,
            persist_output=True,
            required_context_keys=("user_id", "session_id"),
        ),
        ui_spec=ToolUISpecMeta(display_name="浏览器填表"),
        output_cache_spec=_OPAQUE_OUTPUT_CACHE,
        failure_reason="Sandbox Browser Fill Failed",
    ))
    @mcp.tool(name="browser_fill", description="Fill an input field by selector or index.")
    async def browser_fill(
        ctx: Context,
        text: Annotated[str, Field(min_length=1, description="Text to enter.")],
        selector: str | None = None,
        index: int | None = None,
    ) -> BrowserActionResult:
        user_id, session_id = _session_context(ctx)
        result = await browser_service.fill(
            user_id=user_id, session_id=session_id, text=text, selector=selector, index=index,
        )
        return result

    @wisepen_mcp_meta(ToolMeta(
        tool_name="browser_press_key",
        policy=ToolPolicyMeta(
            expose_by_default=True,
            risk_level=ToolRiskLevel.MEDIUM,
            timeout_seconds=60.0,
            persist_output=True,
            required_context_keys=("user_id", "session_id"),
        ),
        ui_spec=ToolUISpecMeta(display_name="浏览器按键"),
        output_cache_spec=_OPAQUE_OUTPUT_CACHE,
        failure_reason="Sandbox Browser Key Press Failed",
    ))
    @mcp.tool(name="browser_press_key", description="Press a single key on the page.")
    async def browser_press_key(ctx: Context, key: Annotated[str, Field(min_length=1)]) -> BrowserActionResult:
        user_id, session_id = _session_context(ctx)
        return await browser_service.press_key(user_id=user_id, session_id=session_id, key=key)

    @wisepen_mcp_meta(ToolMeta(
        tool_name="browser_hotkey",
        policy=ToolPolicyMeta(
            expose_by_default=True,
            risk_level=ToolRiskLevel.MEDIUM,
            timeout_seconds=60.0,
            persist_output=True,
            required_context_keys=("user_id", "session_id"),
        ),
        ui_spec=ToolUISpecMeta(display_name="浏览器组合键"),
        output_cache_spec=_OPAQUE_OUTPUT_CACHE,
        failure_reason="Sandbox Browser Hotkey Failed",
    ))
    @mcp.tool(name="browser_hotkey", description="Press a key combination, e.g. [\"ctrl\", \"c\"].")
    async def browser_hotkey(
        ctx: Context,
        keys: Annotated[list[str], Field(min_length=1, description="Ordered key names.")],
    ) -> BrowserActionResult:
        user_id, session_id = _session_context(ctx)
        return await browser_service.hot_key(user_id=user_id, session_id=session_id, keys=keys)

    @wisepen_mcp_meta(ToolMeta(
        tool_name="browser_scroll",
        policy=ToolPolicyMeta(
            expose_by_default=True,
            risk_level=ToolRiskLevel.MEDIUM,
            timeout_seconds=60.0,
            persist_output=True,
            required_context_keys=("user_id", "session_id"),
        ),
        ui_spec=ToolUISpecMeta(display_name="浏览器滚动"),
        output_cache_spec=_OPAQUE_OUTPUT_CACHE,
        failure_reason="Sandbox Browser Scroll Failed",
    ))
    @mcp.tool(name="browser_scroll", description="Scroll the page in a direction by an amount.")
    async def browser_scroll(
        ctx: Context,
        direction: str | None = None,
        amount: int | None = None,
    ) -> BrowserActionResult:
        user_id, session_id = _session_context(ctx)
        return await browser_service.scroll(user_id=user_id, session_id=session_id, direction=direction, amount=amount)

    @wisepen_mcp_meta(ToolMeta(
        tool_name="browser_hover",
        policy=ToolPolicyMeta(
            expose_by_default=True,
            risk_level=ToolRiskLevel.MEDIUM,
            timeout_seconds=60.0,
            persist_output=True,
            required_context_keys=("user_id", "session_id"),
        ),
        ui_spec=ToolUISpecMeta(display_name="浏览器悬停"),
        output_cache_spec=_OPAQUE_OUTPUT_CACHE,
        failure_reason="Sandbox Browser Hover Failed",
    ))
    @mcp.tool(name="browser_hover", description="Hover over an element by selector or coordinates.")
    async def browser_hover(
        ctx: Context,
        selector: str | None = None,
        x: float | None = None,
        y: float | None = None,
    ) -> BrowserActionResult:
        user_id, session_id = _session_context(ctx)
        return await browser_service.hover(user_id=user_id, session_id=session_id, selector=selector, x=x, y=y)

    @wisepen_mcp_meta(ToolMeta(
        tool_name="browser_back",
        policy=ToolPolicyMeta(
            expose_by_default=True,
            risk_level=ToolRiskLevel.MEDIUM,
            timeout_seconds=60.0,
            persist_output=True,
            required_context_keys=("user_id", "session_id"),
        ),
        ui_spec=ToolUISpecMeta(display_name="浏览器后退"),
        output_cache_spec=_OPAQUE_OUTPUT_CACHE,
        failure_reason="Sandbox Browser Back Failed",
    ))
    @mcp.tool(name="browser_back", description="Go back in browser history.")
    async def browser_back(ctx: Context) -> BrowserActionResult:
        user_id, session_id = _session_context(ctx)
        return await browser_service.back(user_id=user_id, session_id=session_id)

    @wisepen_mcp_meta(ToolMeta(
        tool_name="browser_forward",
        policy=ToolPolicyMeta(
            expose_by_default=True,
            risk_level=ToolRiskLevel.MEDIUM,
            timeout_seconds=60.0,
            persist_output=True,
            required_context_keys=("user_id", "session_id"),
        ),
        ui_spec=ToolUISpecMeta(display_name="浏览器前进"),
        output_cache_spec=_OPAQUE_OUTPUT_CACHE,
        failure_reason="Sandbox Browser Forward Failed",
    ))
    @mcp.tool(name="browser_forward", description="Go forward in browser history.")
    async def browser_forward(ctx: Context) -> BrowserActionResult:
        user_id, session_id = _session_context(ctx)
        return await browser_service.forward(user_id=user_id, session_id=session_id)

    @wisepen_mcp_meta(ToolMeta(
        tool_name="browser_reload",
        policy=ToolPolicyMeta(
            expose_by_default=True,
            risk_level=ToolRiskLevel.MEDIUM,
            timeout_seconds=60.0,
            persist_output=True,
            required_context_keys=("user_id", "session_id"),
        ),
        ui_spec=ToolUISpecMeta(display_name="浏览器刷新"),
        output_cache_spec=_OPAQUE_OUTPUT_CACHE,
        failure_reason="Sandbox Browser Reload Failed",
    ))
    @mcp.tool(name="browser_reload", description="Reload the current page.")
    async def browser_reload(ctx: Context) -> BrowserActionResult:
        user_id, session_id = _session_context(ctx)
        return await browser_service.reload(user_id=user_id, session_id=session_id)

    @wisepen_mcp_meta(ToolMeta(
        tool_name="browser_evaluate",
        policy=ToolPolicyMeta(
            expose_by_default=True,
            risk_level=ToolRiskLevel.HIGH,
            timeout_seconds=60.0,
            persist_output=True,
            required_context_keys=("user_id", "session_id"),
        ),
        ui_spec=ToolUISpecMeta(display_name="浏览器执行 JS"),
        output_cache_spec=_OPAQUE_OUTPUT_CACHE,
        failure_reason="Sandbox Browser Evaluate Failed",
    ))
    @mcp.tool(name="browser_evaluate", description="Execute JavaScript in the current page and return the result.")
    async def browser_evaluate(
        ctx: Context,
        expression: Annotated[str, Field(min_length=1, description="JavaScript expression.")],
    ) -> BrowserActionResult:
        user_id, session_id = _session_context(ctx)
        return await browser_service.evaluate(user_id=user_id, session_id=session_id, expression=expression)

    @wisepen_mcp_meta(ToolMeta(
        tool_name="browser_execute_action",
        policy=ToolPolicyMeta(
            expose_by_default=True,
            risk_level=ToolRiskLevel.MEDIUM,
            timeout_seconds=60.0,
            persist_output=True,
            required_context_keys=("user_id", "session_id"),
        ),
        ui_spec=ToolUISpecMeta(display_name="浏览器 GUI 动作"),
        output_cache_spec=_OPAQUE_OUTPUT_CACHE,
        failure_reason="Sandbox Browser Action Failed",
    ))
    @mcp.tool(
        name="browser_execute_action",
        description=(
            "Execute a raw GUI action on the browser display. "
            "action_type selects the action; required fields depend on action_type "
            "(see the input schema). Supported action types: MOVE_TO, MOVE_REL, CLICK, "
            "MOUSE_DOWN, MOUSE_UP, RIGHT_CLICK, DOUBLE_CLICK, DRAG_TO, DRAG_REL, SCROLL, "
            "TYPING, PRESS, KEY_DOWN, KEY_UP, HOTKEY, WAIT."
        ),
    )
    async def browser_execute_action(
        ctx: Context,
        action: BrowserAction,
    ) -> BrowserActionResult:
        user_id, session_id = _session_context(ctx)
        payload = action if isinstance(action, dict) else action.model_dump(exclude_none=True)
        return await browser_service.execute_action(user_id=user_id, session_id=session_id, action=payload)

    @wisepen_mcp_meta(ToolMeta(
        tool_name="browser_list_tabs",
        policy=ToolPolicyMeta(
            expose_by_default=True,
            risk_level=ToolRiskLevel.LOW,
            timeout_seconds=30.0,
            persist_output=True,
            required_context_keys=("user_id", "session_id"),
        ),
        ui_spec=ToolUISpecMeta(display_name="列出浏览器标签页"),
        output_cache_spec=_OPAQUE_OUTPUT_CACHE,
        failure_reason="Sandbox Browser Tab List Failed",
    ))
    @mcp.tool(name="browser_list_tabs", description="List all open browser tabs.")
    async def browser_list_tabs(ctx: Context) -> BrowserListResult:
        user_id, session_id = _session_context(ctx)
        return await browser_service.list_tabs(user_id=user_id, session_id=session_id)

    @wisepen_mcp_meta(ToolMeta(
        tool_name="browser_new_tab",
        policy=ToolPolicyMeta(
            expose_by_default=True,
            risk_level=ToolRiskLevel.MEDIUM,
            timeout_seconds=60.0,
            persist_output=True,
            required_context_keys=("user_id", "session_id"),
        ),
        ui_spec=ToolUISpecMeta(display_name="新建浏览器标签页"),
        output_cache_spec=_OPAQUE_OUTPUT_CACHE,
        failure_reason="Sandbox Browser Tab Create Failed",
    ))
    @mcp.tool(name="browser_new_tab", description="Create a new browser tab, optionally navigating to a URL.")
    async def browser_new_tab(ctx: Context, url: str | None = None) -> BrowserActionResult:
        user_id, session_id = _session_context(ctx)
        return await browser_service.create_tab(user_id=user_id, session_id=session_id, url=url)

    @wisepen_mcp_meta(ToolMeta(
        tool_name="browser_switch_tab",
        policy=ToolPolicyMeta(
            expose_by_default=True,
            risk_level=ToolRiskLevel.MEDIUM,
            timeout_seconds=30.0,
            persist_output=True,
            required_context_keys=("user_id", "session_id"),
        ),
        ui_spec=ToolUISpecMeta(display_name="切换浏览器标签页"),
        output_cache_spec=_OPAQUE_OUTPUT_CACHE,
        failure_reason="Sandbox Browser Tab Switch Failed",
    ))
    @mcp.tool(name="browser_switch_tab", description="Activate a browser tab by index.")
    async def browser_switch_tab(ctx: Context, index: Annotated[int, Field(ge=0)]) -> BrowserActionResult:
        user_id, session_id = _session_context(ctx)
        return await browser_service.activate_tab(user_id=user_id, session_id=session_id, index=index)

    @wisepen_mcp_meta(ToolMeta(
        tool_name="browser_close_tab",
        policy=ToolPolicyMeta(
            expose_by_default=True,
            risk_level=ToolRiskLevel.MEDIUM,
            timeout_seconds=30.0,
            persist_output=True,
            required_context_keys=("user_id", "session_id"),
        ),
        ui_spec=ToolUISpecMeta(display_name="关闭浏览器标签页"),
        output_cache_spec=_OPAQUE_OUTPUT_CACHE,
        failure_reason="Sandbox Browser Tab Close Failed",
    ))
    @mcp.tool(name="browser_close_tab", description="Close a browser tab by index.")
    async def browser_close_tab(ctx: Context, index: Annotated[int, Field(ge=0)]) -> BrowserActionResult:
        user_id, session_id = _session_context(ctx)
        return await browser_service.close_tab(user_id=user_id, session_id=session_id, index=index)

    @wisepen_mcp_meta(ToolMeta(
        tool_name="browser_get_cookies",
        policy=ToolPolicyMeta(
            expose_by_default=True,
            risk_level=ToolRiskLevel.MEDIUM,
            timeout_seconds=30.0,
            persist_output=True,
            required_context_keys=("user_id", "session_id"),
        ),
        ui_spec=ToolUISpecMeta(display_name="读取浏览器 Cookie"),
        output_cache_spec=_OPAQUE_OUTPUT_CACHE,
        failure_reason="Sandbox Browser Cookie Read Failed",
    ))
    @mcp.tool(name="browser_get_cookies", description="Get browser cookies, optionally filtered by comma-separated URLs.")
    async def browser_get_cookies(ctx: Context, urls: str | None = None) -> BrowserListResult:
        user_id, session_id = _session_context(ctx)
        return await browser_service.get_cookies(user_id=user_id, session_id=session_id, urls=urls)

    @wisepen_mcp_meta(ToolMeta(
        tool_name="browser_set_cookies",
        policy=ToolPolicyMeta(
            expose_by_default=True,
            risk_level=ToolRiskLevel.MEDIUM,
            timeout_seconds=30.0,
            persist_output=True,
            required_context_keys=("user_id", "session_id"),
        ),
        ui_spec=ToolUISpecMeta(display_name="写入浏览器 Cookie"),
        output_cache_spec=_OPAQUE_OUTPUT_CACHE,
        failure_reason="Sandbox Browser Cookie Write Failed",
    ))
    @mcp.tool(name="browser_set_cookies", description="Set browser cookies from a list of cookie objects.")
    async def browser_set_cookies(
        ctx: Context,
        cookies: Annotated[list[dict], Field(min_length=1, description="Cookie objects with name/value/domain/path.")],
    ) -> BrowserActionResult:
        user_id, session_id = _session_context(ctx)
        return await browser_service.set_cookies(user_id=user_id, session_id=session_id, cookies=cookies)

    @wisepen_mcp_meta(ToolMeta(
        tool_name="browser_clear_cookies",
        policy=ToolPolicyMeta(
            expose_by_default=True,
            risk_level=ToolRiskLevel.MEDIUM,
            timeout_seconds=30.0,
            persist_output=True,
            required_context_keys=("user_id", "session_id"),
        ),
        ui_spec=ToolUISpecMeta(display_name="清空浏览器 Cookie"),
        output_cache_spec=_OPAQUE_OUTPUT_CACHE,
        failure_reason="Sandbox Browser Cookie Clear Failed",
    ))
    @mcp.tool(name="browser_clear_cookies", description="Clear all browser cookies.")
    async def browser_clear_cookies(ctx: Context) -> BrowserActionResult:
        user_id, session_id = _session_context(ctx)
        return await browser_service.clear_cookies(user_id=user_id, session_id=session_id)

    return mcp
