from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import PurePosixPath
import re
from tempfile import SpooledTemporaryFile
from typing import BinaryIO, Literal
from urllib.parse import quote

import httpx

from common.core.exceptions import ServiceException, RpcError
from common.logger import info
from pydantic import BaseModel

from sandbox.application.workspace_manager import WorkspaceManager
from sandbox.core.config.app_settings import settings
from sandbox.core.providers import SandboxProviderManager
from sandbox.domain.entities import SandboxDocument, SandboxState, SessionWorkspaceDocument
from sandbox.domain.error_codes import SandboxErrorCode
from sandbox.domain.interfaces import (
    FileContentEncoding,
    FileDownloadResult,
    FileFindResult,
    FileGlobResult,
    FileGlobSortField,
    FileGrepResult,
    FileListResult,
    FileListSortField,
    FileReadResult,
    FileReplaceResult,
    FileSearchResult,
    FileTransferTooLargeError,
    FileWriteResult,
)
from sandbox.domain.repositories import SandboxRepository
from sandbox.service_client import (
    DocumentServiceClient,
    FileStorageClient,
    WebAppServiceClient,
)

_OSS_CALLBACK_HEADER = "x-oss-callback"
_DOCUMENT_RESOURCE_TYPES = {
    "pdf": "PDF",
    "doc": "DOC",
    "docx": "DOCX",
    "ppt": "PPT",
    "pptx": "PPTX",
    "xls": "XLS",
    "xlsx": "XLSX",
}
_WEB_APP_EXTENSIONS = frozenset({"html", "htm"})


class TemporaryAttachment(BaseModel):
    attachment_id: str
    attachment_name: str
    object_key: str
    file_size: int
    deleted: bool = False


class AttachmentImportResult(BaseModel):
    attachment_id: str
    path: str
    size: int


class ObjectStorageDownloadResult(BaseModel):
    filename: str
    path: str
    object_key: str
    size: int
    download_url: str
    expires_in_seconds: int


class CloudResourceUploadResult(BaseModel):
    path: str
    filename: str
    resource_type: str
    upload_task_id: str
    version: int | None = None
    flash_uploaded: bool
    status: Literal["SUBMITTED"] = "SUBMITTED"
    size: int


@dataclass(frozen=True)
class _WorkspaceFile:
    path: str
    filename: str
    source: BinaryIO
    info: FileDownloadResult


class SandboxFileService:
    """Typed adapter between session workspaces, AIO File API and File Storage/OSS."""

    def __init__(
        self,
        sandbox_repository: SandboxRepository,
        workspace_manager: WorkspaceManager,
        provider_manager: SandboxProviderManager,
        file_storage_client: FileStorageClient,
        document_service_client: DocumentServiceClient,
        web_app_service_client: WebAppServiceClient,
    ) -> None:
        self._sandboxes = sandbox_repository
        self._workspace_manager = workspace_manager
        self._providers = provider_manager
        self._file_storage = file_storage_client
        self._documents = document_service_client
        self._web_apps = web_app_service_client

    async def read_file(
        self,
        *,
        user_id: str,
        session_id: str,
        path: str,
        start_line: int | None = None,
        end_line: int | None = None,
    ) -> FileReadResult:
        async with self._workspace_lease(user_id, session_id) as (workspace, sandbox):
            # 把 MCP 提供的 workspace 相对路径转换为 AIO 容器内的绝对路径
            absolute_path, _ = self._resolve_workspace_path(workspace.workspace_path, path)
            result = await self._providers.read_file(
                sandbox.provider_id,
                sandbox.base_url,
                absolute_path,
                start_line=start_line,
                end_line=end_line,
                provider_metadata=sandbox.metadata or {},
            )
        # 把 AIO 返回的容器绝对路径转换成 workspace 相对路径
        return result.model_copy(update={
            "path": self._relative_provider_path(workspace.workspace_path, result.path),
        })

    async def write_file(
        self,
        *,
        user_id: str,
        session_id: str,
        path: str,
        content: str,
        encoding: FileContentEncoding = FileContentEncoding.UTF8,
        append: bool = False,
        leading_newline: bool = False,
        trailing_newline: bool = False,
    ) -> FileWriteResult:
        async with self._workspace_lease(user_id, session_id) as (workspace, sandbox):
            # 把 MCP 提供的 workspace 相对路径转换为 AIO 容器内的绝对路径
            absolute_path, _ = self._resolve_workspace_path(workspace.workspace_path, path)
            result = await self._providers.write_file(
                sandbox.provider_id,
                sandbox.base_url,
                absolute_path,
                content,
                encoding=encoding,
                append=append,
                leading_newline=leading_newline,
                trailing_newline=trailing_newline,
                provider_metadata=sandbox.metadata or {},
            )
        # 把 AIO 返回的容器绝对路径转换成 workspace 相对路径
        return result.model_copy(update={
            "path": self._relative_provider_path(workspace.workspace_path, result.path),
        })

    async def replace_in_file(
        self,
        *,
        user_id: str,
        session_id: str,
        path: str,
        old_str: str,
        new_str: str,
    ) -> FileReplaceResult:
        async with self._workspace_lease(user_id, session_id) as (workspace, sandbox):
            # 把 MCP 提供的 workspace 相对路径转换为 AIO 容器内的绝对路径
            absolute_path, relative_path = self._resolve_workspace_path(workspace.workspace_path, path)
            result = await self._providers.replace_in_file(
                sandbox.provider_id,
                sandbox.base_url,
                absolute_path,
                old_str,
                new_str,
                provider_metadata=sandbox.metadata or {},
            )
        # 把 AIO 返回的容器绝对路径转换成 workspace 相对路径
        return result.model_copy(update={
            "path": self._relative_provider_path(workspace.workspace_path, result.path),
        })

    async def search_in_file(
        self,
        *,
        user_id: str,
        session_id: str,
        path: str,
        regex: str,
    ) -> FileSearchResult:
        async with self._workspace_lease(user_id, session_id) as (workspace, sandbox):
            # 把 MCP 提供的 workspace 相对路径转换为 AIO 容器内的绝对路径
            absolute_path, _ = self._resolve_workspace_path(workspace.workspace_path, path)
            result = await self._providers.search_in_file(
                sandbox.provider_id,
                sandbox.base_url,
                absolute_path,
                regex,
                provider_metadata=sandbox.metadata or {},
            )
        # 把 AIO 返回的容器绝对路径转换成 workspace 相对路径
        return result.model_copy(update={
            "path": self._relative_provider_path(workspace.workspace_path, result.path),
        })

    async def find_files(
        self,
        *,
        user_id: str,
        session_id: str,
        path: str,
        glob: str,
    ) -> FileFindResult:
        async with self._workspace_lease(user_id, session_id) as (workspace, sandbox):
            # 把 MCP 提供的 workspace 相对路径转换为 AIO 容器内的绝对路径
            absolute_path, _ = self._resolve_workspace_path(workspace.workspace_path, path)
            result = await self._providers.find_files(
                sandbox.provider_id,
                sandbox.base_url,
                absolute_path,
                glob,
                provider_metadata=sandbox.metadata or {},
            )
        # 把 AIO 返回的容器绝对路径转换成 workspace 相对路径
        return result.model_copy(update={
            "path": self._relative_provider_path(workspace.workspace_path, result.path),
            "files": [self._relative_provider_path(workspace.workspace_path, item) for item in result.files],
        })

    async def grep_files(
        self,
        *,
        user_id: str,
        session_id: str,
        path: str,
        pattern: str,
        include: tuple[str, ...] | None = None,
        exclude: tuple[str, ...] | None = None,
        case_insensitive: bool = False,
        fixed_strings: bool = False,
        context_before: int = 0,
        context_after: int = 0,
        max_results: int = 500,
        max_file_size: str = "1M",
        multiline: bool = False,
        offset: int = 0,
        file_type: str | None = None,
        recursive: bool = True,
    ) -> FileGrepResult:
        async with self._workspace_lease(user_id, session_id) as (workspace, sandbox):
            # 把 MCP 提供的 workspace 相对路径转换为 AIO 容器内的绝对路径
            absolute_path, _ = self._resolve_workspace_path(workspace.workspace_path, path)
            result = await self._providers.grep_files(
                sandbox.provider_id,
                sandbox.base_url,
                absolute_path,
                pattern,
                include=include,
                exclude=exclude,
                case_insensitive=case_insensitive,
                fixed_strings=fixed_strings,
                context_before=context_before,
                context_after=context_after,
                max_results=max_results,
                max_file_size=max_file_size,
                multiline=multiline,
                offset=offset,
                file_type=file_type,
                recursive=recursive,
                provider_metadata=sandbox.metadata or {},
            )

        # 把 AIO 返回的容器绝对路径转换成 workspace 相对路径
        return result.model_copy(update={
            "path": self._relative_provider_path(workspace.workspace_path, result.path),
            "matches": [
                match.model_copy(update={
                    "path": self._relative_provider_path(workspace.workspace_path, match.path),
                })
                for match in result.matches
            ],
        })

    async def glob_files(
        self,
        *,
        user_id: str,
        session_id: str,
        path: str,
        pattern: str,
        exclude: tuple[str, ...] | None = None,
        include_hidden: bool = False,
        files_only: bool = True,
        include_metadata: bool = True,
        max_results: int = 5000,
        sort_by: FileGlobSortField = FileGlobSortField.PATH,
        sort_desc: bool = False,
    ) -> FileGlobResult:
        async with self._workspace_lease(user_id, session_id) as (workspace, sandbox):
            # 把 MCP 提供的 workspace 相对路径转换为 AIO 容器内的绝对路径
            absolute_path, _ = self._resolve_workspace_path(workspace.workspace_path, path)
            result = await self._providers.glob_files(
                sandbox.provider_id,
                sandbox.base_url,
                absolute_path,
                pattern,
                exclude=exclude,
                include_hidden=include_hidden,
                files_only=files_only,
                include_metadata=include_metadata,
                max_results=max_results,
                sort_by=sort_by,
                sort_desc=sort_desc,
                provider_metadata=sandbox.metadata or {},
            )
        # 把 AIO 返回的容器绝对路径转换成 workspace 相对路径
        return result.model_copy(update={
            "path": self._relative_provider_path(workspace.workspace_path, result.path),
            "files": [
                entry.model_copy(update={
                    "path": self._relative_provider_path(workspace.workspace_path, entry.path),
                })
                for entry in result.files
            ],
        })

    async def list_files(
        self,
        *,
        user_id: str,
        session_id: str,
        path: str,
        recursive: bool = False,
        show_hidden: bool = True,
        file_types: tuple[str, ...] | None = None,
        max_depth: int | None = None,
        include_size: bool = True,
        include_permissions: bool = False,
        sort_by: FileListSortField = FileListSortField.NAME,
        sort_desc: bool = False,
    ) -> FileListResult:
        async with self._workspace_lease(user_id, session_id) as (workspace, sandbox):
            # 把 MCP 提供的 workspace 相对路径转换为 AIO 容器内的绝对路径
            absolute_path, _ = self._resolve_workspace_path(workspace.workspace_path, path)
            result = await self._providers.list_files(
                sandbox.provider_id,
                sandbox.base_url,
                absolute_path,
                recursive=recursive,
                show_hidden=show_hidden,
                file_types=file_types,
                max_depth=max_depth,
                include_size=include_size,
                include_permissions=include_permissions,
                sort_by=sort_by,
                sort_desc=sort_desc,
                provider_metadata=sandbox.metadata or {},
            )
        # 把 AIO 返回的容器绝对路径转换成 workspace 相对路径
        return result.model_copy(update={
            "path": self._relative_provider_path(workspace.workspace_path, result.path),
            "files": [
                entry.model_copy(update={
                    "path": self._relative_provider_path(workspace.workspace_path, entry.path),
                })
                for entry in result.files
            ],
        })

    async def upload_file(
        self,
        *,
        user_id: str,
        session_id: str,
        attachment_id: str,
        destination_path: str,
        temporary_attachment_refs: Sequence[TemporaryAttachment],
    ) -> AttachmentImportResult:
        attachment = self._find_attachment(temporary_attachment_refs, attachment_id)
        if attachment.file_size > settings.SANDBOX_FILE_MAX_BYTES:
            raise ServiceException(SandboxErrorCode.FILE_TOO_LARGE)

        async with self._workspace_lease(user_id, session_id) as (workspace, sandbox):
            # 把 MCP 提供的 workspace 相对路径转换为 AIO 容器内的绝对路径
            absolute_path, relative_path = self._resolve_workspace_path(workspace.workspace_path, destination_path)
            # 从 OSS 下载文件到容器
            download_url = await self._file_storage.get_download_url(
                attachment.object_key,
                duration_seconds=settings.SANDBOX_FILE_DOWNLOAD_URL_DURATION_SECONDS,
            )
            try:
                # 创建可自动切换到磁盘的临时文件
                with SpooledTemporaryFile(
                    max_size=settings.SANDBOX_FILE_SPOOL_MAX_MEMORY_BYTES,
                    mode="w+b",
                ) as temporary_file:
                    # 从 File Storage 提供的预签名 OSS URL 流式读取内容，并写入 temporary_file
                    size = await self._download_to_file(
                        download_url,
                        temporary_file,
                        max_bytes=settings.SANDBOX_FILE_MAX_BYTES,
                    )
                    # 把临时文件上传到 AIO
                    await self._providers.upload_file(
                        sandbox.provider_id,
                        sandbox.base_url,
                        absolute_path,
                        temporary_file,
                        attachment.attachment_name,
                        provider_metadata=sandbox.metadata or {},
                    )
            except FileTransferTooLargeError as exc:
                raise ServiceException(SandboxErrorCode.FILE_TOO_LARGE) from exc

        info("attachment imported into sandbox workspace.",
            user_id=user_id,
            session_id=session_id,
            attachment_id=attachment_id,
            path=relative_path,
            bytes=size,
        )
        return AttachmentImportResult(attachment_id=attachment_id, path=relative_path,size=size)

    async def upload_file_to_cloud_drive(
        self,
        *,
        user_id: str,
        session_id: str,
        path: str,
    ) -> CloudResourceUploadResult:
        async with self._open_workspace_file(user_id=user_id, session_id=session_id, path=path) as workspace_file:
            extension = self._extension(workspace_file.filename)
            if extension in _DOCUMENT_RESOURCE_TYPES:
                # 向 Document Service 初始化 OSS 上传
                resource_type = _DOCUMENT_RESOURCE_TYPES[extension]
                ticket = await self._documents.init_upload(
                    filename=workspace_file.filename,
                    extension=extension,
                    md5=workspace_file.info.md5,
                    expected_size=workspace_file.info.size,
                )
                upload_task_id = ticket.document_id
                version = None
            elif extension in _WEB_APP_EXTENSIONS:
                # 向 Web App Service 初始化 OSS 上传
                resource_type = "SINGLE_HTML_PAGE"
                ticket = await self._web_apps.init_upload(
                    filename=workspace_file.filename,
                    md5=workspace_file.info.md5,
                    expected_size=workspace_file.info.size,
                )
                upload_task_id = ticket.single_html_page_id
                version = ticket.version
            else:
                raise ServiceException(SandboxErrorCode.FILE_TYPE_UNSUPPORTED)

            if not ticket.flash_uploaded:
                if not ticket.put_url:
                    raise RuntimeError("Resource service did not return an OSS upload URL")
                # 将临时文件上传到 OSS
                await self._upload_from_file(
                    ticket.put_url,
                    workspace_file.source,
                    size=workspace_file.info.size,
                    callback_header=ticket.callback_header,
                )

            result = CloudResourceUploadResult(
                path=workspace_file.path,
                filename=workspace_file.filename,
                resource_type=resource_type,
                upload_task_id=upload_task_id,
                version=version,
                flash_uploaded=ticket.flash_uploaded,
                size=workspace_file.info.size,
            )

        info(
            "sandbox workspace file submitted as cloud resource.",
            user_id=user_id,
            session_id=session_id,
            path=result.path,
            resource_type=result.resource_type,
            upload_task_id=result.upload_task_id,
            bytes=result.size,
        )
        return result

    async def download_file(
        self,
        *,
        user_id: str,
        session_id: str,
        path: str,
    ) -> ObjectStorageDownloadResult:
        async with self._open_workspace_file(user_id=user_id, session_id=session_id, path=path) as workspace_file:
            # 向 File Storage Service 初始化 OSS 上传
            upload = await self._file_storage.init_upload(
                md5=workspace_file.info.md5,
                extension=self._extension(workspace_file.filename),
                scene="PRIVATE_AI_ATTACHMENT",
                biz_tag=f"{user_id}/{session_id}/sandbox-output",
                expected_size=workspace_file.info.size,
            )
            if not upload.flash_uploaded:
                if not upload.put_url:
                    raise RuntimeError("File Storage did not return an OSS upload URL")
                # 将临时文件上传到 OSS
                await self._upload_from_file(
                    upload.put_url,
                    workspace_file.source,
                    size=workspace_file.info.size,
                    callback_header=upload.callback_header,
                )
            # 构建下载文件名响应头
            content_disposition = f"attachment; filename*=UTF-8''{quote(workspace_file.filename)}"
            # 获取 OSS 预签名下载地址
            download_url = await self._file_storage.get_download_url(
                upload.object_key,
                duration_seconds=settings.SANDBOX_FILE_DOWNLOAD_URL_DURATION_SECONDS,
                content_disposition=content_disposition,
            )
            result = ObjectStorageDownloadResult(
                filename=workspace_file.filename,
                path=workspace_file.path,
                object_key=upload.object_key,
                size=workspace_file.info.size,
                download_url=download_url,
                expires_in_seconds=settings.SANDBOX_FILE_DOWNLOAD_URL_DURATION_SECONDS,
            )

        info(
            "sandbox workspace file exported to object storage.",
            user_id=user_id,
            session_id=session_id,
            path=result.path,
            object_key=result.object_key,
            bytes=result.size,
        )
        return result

    @asynccontextmanager
    async def _open_workspace_file(
        self,
        *,
        user_id: str,
        session_id: str,
        path: str,
    ) -> AsyncIterator[_WorkspaceFile]:
        async with self._workspace_lease(user_id, session_id) as (workspace, sandbox):
            absolute_path, relative_path = self._resolve_workspace_path(workspace.workspace_path, path)
            filename = PurePosixPath(relative_path).name
            if not filename or filename in {".", ".."}:
                raise ServiceException(SandboxErrorCode.FILE_PATH_INVALID)

            try:
                # 创建可自动切换到磁盘的临时文件
                with SpooledTemporaryFile(
                    max_size=settings.SANDBOX_FILE_SPOOL_MAX_MEMORY_BYTES,
                    mode="w+b",
                ) as temporary_file:
                    # 从 AIO workspace 下载文件
                    file_info = await self._providers.download_file(
                        sandbox.provider_id,
                        sandbox.base_url,
                        absolute_path,
                        temporary_file,
                        settings.SANDBOX_FILE_MAX_BYTES,
                        provider_metadata=sandbox.metadata or {},
                    )
                    yield _WorkspaceFile(
                        path=relative_path,
                        filename=filename,
                        source=temporary_file,
                        info=file_info,
                    )
            except FileTransferTooLargeError as exc:
                raise ServiceException(SandboxErrorCode.FILE_TOO_LARGE) from exc

    @asynccontextmanager
    async def _workspace_lease(
        self,
        user_id: str,
        session_id: str,
    ) -> AsyncIterator[tuple[SessionWorkspaceDocument, SandboxDocument]]:
        workspace = await self._workspace_manager.allocate(user_id, session_id)
        sandbox = await self._sandboxes.get_by_id(workspace.sandbox_id)
        if sandbox is None or sandbox.state != SandboxState.USER_ACTIVE or sandbox.bind_user_id != user_id:
            raise ServiceException(SandboxErrorCode.SANDBOX_UNAVAILABLE)

        await self._sandboxes.register_active_command(
            sandbox.sandbox_id, user_id, datetime.now(timezone.utc),
        )

        try:
            yield workspace, sandbox
        finally:
            await self._sandboxes.unregister_active_command(sandbox.sandbox_id)

    async def _download_to_file(
        self,
        url: str,
        destination: BinaryIO,
        *,
        max_bytes: int,
    ) -> int:
        total = 0 # 初始化实际字节计数
        timeout = httpx.Timeout(settings.SANDBOX_FILE_TRANSFER_TIMEOUT_SECONDS)
        # 创建 HTTP 客户端
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            async with client.stream("GET", url) as response: # 使用流式 GET
                response.raise_for_status()
                content_length = response.headers.get("content-length") # 下载前检查 Content-Length
                try:
                    declared_size = int(content_length) if content_length else None
                except ValueError:
                    declared_size = None
                if declared_size is not None and declared_size > max_bytes: # 如果超过最大允许大小
                    raise FileTransferTooLargeError(max_bytes)
                async for chunk in response.aiter_bytes(): # 分块读取并执行真实大小检查
                    total += len(chunk)
                    if total > max_bytes:
                        raise FileTransferTooLargeError(max_bytes)
                    destination.write(chunk)
        destination.seek(0) # 把文件指针移回开头
        return total

    async def _upload_from_file(
        self,
        put_url: str,
        source: BinaryIO,
        *,
        size: int,
        callback_header: str | None = None,
    ) -> None:
        source.seek(0)

        async def chunks():
            while True:
                chunk = await asyncio.to_thread(source.read, 64 * 1024)
                if not chunk:
                    return
                yield chunk

        # 构建 OSS PUT headers
        headers = {
            "Content-Type": "application/octet-stream",
            "Content-Length": str(size),
        }
        if callback_header: # 添加 OSS callback header
            headers[_OSS_CALLBACK_HEADER] = callback_header

        timeout = httpx.Timeout(settings.SANDBOX_FILE_TRANSFER_TIMEOUT_SECONDS)
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            response = await client.put(put_url, content=chunks(), headers=headers) # 通过异步生成器执行 PUT

        if callback_header and response.status_code != 200: # 非 callback 模式接受所有 2xx
            raise RpcError(
                service_name="oss",
                path=put_url,
                status=response.status_code,
                msg=f"OSS callback upload failed: status={response.status_code} body={response.text[:500]!r}"
            )

        if response.status_code < 200 or response.status_code >= 300:
            raise RpcError(
                service_name="oss",
                path=put_url,
                status=response.status_code,
                msg=f"OSS upload failed: status={response.status_code} body={response.text[:500]!r}"
            )

    @staticmethod
    def _resolve_workspace_path(workspace_path: str, path: str) -> tuple[str, str]:
        # 把 MCP 提供的 workspace 相对路径，安全地转换为 AIO 容器内的绝对路径
        if not isinstance(path, str):
            raise ServiceException(SandboxErrorCode.FILE_PATH_INVALID)
        value = path.strip() # 清除首尾空白
        if not value or "\x00" in value or "\\" in value: # 拒绝空路径、NUL 和反斜杠，当前容器仅支持 Linux 环境，只允许 / 作为路径分隔符
            raise ServiceException(SandboxErrorCode.FILE_PATH_INVALID)

        relative = PurePosixPath(value)
        if relative.is_absolute() or ".." in relative.parts: # 拒绝绝对路径，只能提交当前 workspace 下的相对路径或 .. 路径穿越
            raise ServiceException(SandboxErrorCode.FILE_PATH_INVALID)

        normalized = relative.as_posix() # 规范化路径
        if normalized == ".": # 特殊处理 workspace 根目录
            return str(PurePosixPath(workspace_path)), "."
        # 拼接最终绝对路径
        return str(PurePosixPath(workspace_path) / relative), normalized

    @staticmethod
    def _relative_provider_path(workspace_path: str, value: str) -> str:
        # 把 AIO 返回的容器绝对路径转换成 workspace 相对路径
        root = workspace_path.rstrip("/") # 去掉 workspace 末尾的 /
        if value == root: # AIO 返回 workspace 根目录时
            return "."
        if value.startswith(root + "/"): # AIO 返回 workspace 内部路径时
            return value[len(root) + 1:]
        raise ServiceException(SandboxErrorCode.FILE_PATH_INVALID) # 拒绝 workspace 外路径

    @staticmethod
    def _find_attachment(
        attachments: Sequence[TemporaryAttachment],
        attachment_id: str,
    ) -> TemporaryAttachment:
        if not attachment_id:
            raise ServiceException(SandboxErrorCode.ATTACHMENT_UNAVAILABLE)
        for attachment in attachments:
            if attachment.attachment_id == attachment_id and not attachment.deleted:
                return attachment
        raise ServiceException(SandboxErrorCode.ATTACHMENT_UNAVAILABLE)

    @staticmethod
    def _extension(filename: str) -> str:
        extension = PurePosixPath(filename).suffix.removeprefix(".")
        return extension.lower() if re.compile(r"^[A-Za-z0-9]{1,32}$").fullmatch(extension) else "bin"
