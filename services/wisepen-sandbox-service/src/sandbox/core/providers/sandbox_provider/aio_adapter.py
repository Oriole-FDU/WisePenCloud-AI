from __future__ import annotations

from collections.abc import Mapping
import hashlib
import secrets
import string
from typing import Any, BinaryIO, TypeVar

import httpx
from pydantic import BaseModel, ConfigDict, ValidationError

from sandbox.domain.entities import CommandTaskStatus
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
    FileUploadResult,
    FileWriteResult,
    ProviderCommandHandle,
    ProviderCommandSnapshot,
    SandboxProvision,
    SandboxProvider,
    SandboxProviderType,
)


FileResultT = TypeVar("FileResultT", bound=BaseModel)


class AIOAdapterConfig(BaseModel):
    """AIO Provider 的专属配置"""

    model_config = ConfigDict(extra="forbid")

    image: str
    request_timeout_seconds: float = 5.0
    file_transfer_timeout_seconds: float = 120.0


class AIOAdapter(SandboxProvider):
    """AIO 沙箱 Provider 适配器"""

    _HEALTH_FIELDS = frozenset({"success", "message", "data", "home_dir", "version", "detail"})

    def __init__(
        self,
        image: str,
        request_timeout_seconds: float,
        file_transfer_timeout_seconds: float = 120.0,
    ) -> None:
        self._image = image
        self._request_timeout_seconds = request_timeout_seconds
        self._file_transfer_timeout_seconds = file_transfer_timeout_seconds

    @classmethod
    def sandbox_provider_id(cls) -> SandboxProviderType:
        return SandboxProviderType.AIO

    @classmethod
    def from_config(cls, config: Mapping[str, Any]) -> AIOAdapter:
        adapter_config = AIOAdapterConfig.model_validate(config)
        return cls(
            image=adapter_config.image,
            request_timeout_seconds=adapter_config.request_timeout_seconds,
            file_transfer_timeout_seconds=adapter_config.file_transfer_timeout_seconds,
        )

    def create_provision(self) -> SandboxProvision:
        """生成 AIO 专属的容器环境变量和运行时认证元数据"""
        alphabet = string.ascii_letters + string.digits
        api_key = "".join(secrets.choice(alphabet) for _ in range(48))
        return SandboxProvision(
            image=self._image,
            container_environment={"SANDBOX_API_KEY": api_key},
            provider_metadata={"api_key": api_key},
        )

    @staticmethod
    def _headers(provider_metadata: Mapping[str, object] | None) -> dict[str, str]:
        api_key = (provider_metadata or {}).get("api_key")
        return {"X-AIO-API-Key": str(api_key)} if api_key else {}

    async def check_ready(
        self,
        base_url: str | None,
        provider_metadata: Mapping[str, Any] | None = None,
    ) -> bool:
        # 预热阶段通过 AIO 健康接口确认容器内服务已经可以接受命令
        if base_url is None:
            return False
        try:
            async with httpx.AsyncClient(
                timeout=self._request_timeout_seconds,
                trust_env=False,
                headers=self._headers(provider_metadata),
            ) as client:
                response = await client.get(f"{base_url.rstrip('/')}/v1/sandbox")
            if response.status_code != 200:
                return False
            payload = response.json()
        except (httpx.HTTPError, httpx.InvalidURL, ValueError):
            return False
        return isinstance(payload, dict) and self._HEALTH_FIELDS.issubset(payload)

    async def start_command(
        self,
        base_url: str,
        command: str,
        working_directory: str,
        timeout_seconds: int,
        provider_metadata: Mapping[str, Any] | None = None,
    ) -> ProviderCommandHandle:
        """通过 AIO Bash Pipe 启动命令并返回 Provider 句柄"""
        async with httpx.AsyncClient(
            timeout=self._request_timeout_seconds,
            trust_env=False,
            headers=self._headers(provider_metadata),
        ) as client:
            response = await client.post(
                f"{base_url.rstrip('/')}/v1/bash/exec",
                json={
                    "command": command,
                    "exec_dir": working_directory,
                    "async_mode": True,
                    "hard_timeout": timeout_seconds,
                },
            )
            response.raise_for_status()
            payload = response.json()
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, dict) or not payload.get("success"):
            raise RuntimeError("AIO Bash returned an invalid command handle")
        session_id = data.get("session_id")
        if not isinstance(session_id, str) or not session_id:
            raise RuntimeError("AIO Bash did not return a command session id")
        command_id = data.get("command_id")
        return ProviderCommandHandle(
            provider_session_id=session_id,
            provider_command_id=command_id if isinstance(command_id, str) else None,
        )

    async def get_command_result(
        self,
        base_url: str,
        handle: ProviderCommandHandle,
        stdout_offset: int,
        stderr_offset: int,
        wait_timeout_seconds: float,
        provider_metadata: Mapping[str, Any] | None = None,
    ) -> ProviderCommandSnapshot:
        """读取 AIO 命令的增量输出和状态"""
        async with httpx.AsyncClient(
            timeout=max(self._request_timeout_seconds, wait_timeout_seconds + 2),
            trust_env=False,
            headers=self._headers(provider_metadata),
        ) as client:
            response = await client.post(
                f"{base_url.rstrip('/')}/v1/bash/output",
                json={
                    "session_id": handle.provider_session_id,
                    "command_id": handle.provider_command_id,
                    "offset": stdout_offset,
                    "stderr_offset": stderr_offset,
                    "wait": True,
                    "wait_timeout": wait_timeout_seconds,
                },
            )
            response.raise_for_status()
            payload = response.json()
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, dict) or not payload.get("success"):
            raise RuntimeError("AIO Bash returned an invalid command result")
        stdout = data.get("stdout") or ""
        stderr = data.get("stderr") or ""
        command = data.get("command") or {}
        return ProviderCommandSnapshot(
            status=CommandTaskStatus(command.get("status")),
            exit_code=command.get("exit_code"),
            stdout=stdout,
            stderr=stderr,
            stdout_offset=int(data.get("offset") or stdout_offset + len(stdout.encode("utf-8"))),
            stderr_offset=int(data.get("stderr_offset") or stderr_offset + len(stderr.encode("utf-8"))),
        )

    async def kill_command(
        self,
        base_url: str,
        handle: ProviderCommandHandle,
        provider_metadata: Mapping[str, Any] | None = None,
    ) -> None:
        """主动终止 AIO 命令"""
        async with httpx.AsyncClient(
            timeout=self._request_timeout_seconds,
            trust_env=False,
            headers=self._headers(provider_metadata),
        ) as client:
            response = await client.post(
                f"{base_url.rstrip('/')}/v1/bash/kill",
                json={"session_id": handle.provider_session_id, "signal": "SIGKILL"},
            )
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict) or payload.get("success") is not True:
                raise RuntimeError("AIO Bash rejected command termination")

    async def read_file(
        self,
        base_url: str,
        path: str,
        *,
        start_line: int | None = None,
        end_line: int | None = None,
        provider_metadata: Mapping[str, object] | None = None,
    ) -> FileReadResult:
        return await self._post_file_result(
            base_url,
            "/v1/file/read",
            {
                "file": path,
                "start_line": start_line,
                "end_line": end_line,
                "sudo": False,
            },
            FileReadResult,
            provider_metadata,
        )

    async def write_file(
        self,
        base_url: str,
        path: str,
        content: str,
        *,
        encoding: FileContentEncoding = FileContentEncoding.UTF8,
        append: bool = False,
        leading_newline: bool = False,
        trailing_newline: bool = False,
        provider_metadata: Mapping[str, object] | None = None,
    ) -> FileWriteResult:
        return await self._post_file_result(
            base_url,
            "/v1/file/write",
            {
                "file": path,
                "content": content,
                "encoding": encoding.value,
                "append": append,
                "leading_newline": leading_newline,
                "trailing_newline": trailing_newline,
                "sudo": False,
            },
            FileWriteResult,
            provider_metadata,
        )

    async def replace_in_file(
        self,
        base_url: str,
        path: str,
        old_str: str,
        new_str: str,
        *,
        provider_metadata: Mapping[str, object] | None = None,
    ) -> FileReplaceResult:
        return await self._post_file_result(
            base_url,
            "/v1/file/replace",
            {"file": path, "old_str": old_str, "new_str": new_str, "sudo": False},
            FileReplaceResult,
            provider_metadata,
        )

    async def search_in_file(
        self,
        base_url: str,
        path: str,
        regex: str,
        *,
        provider_metadata: Mapping[str, object] | None = None,
    ) -> FileSearchResult:
        return await self._post_file_result(
            base_url,
            "/v1/file/search",
            {"file": path, "regex": regex, "sudo": False},
            FileSearchResult,
            provider_metadata,
        )

    async def find_files(
        self,
        base_url: str,
        path: str,
        glob: str,
        *,
        provider_metadata: Mapping[str, object] | None = None,
    ) -> FileFindResult:
        return await self._post_file_result(
            base_url,
            "/v1/file/find",
            {"path": path, "glob": glob},
            FileFindResult,
            provider_metadata,
        )

    async def grep_files(
        self,
        base_url: str,
        path: str,
        pattern: str,
        *,
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
        provider_metadata: Mapping[str, object] | None = None,
    ) -> FileGrepResult:
        return await self._post_file_result(
            base_url,
            "/v1/file/grep",
            {
                "path": path,
                "pattern": pattern,
                "include": include,
                "exclude": exclude,
                "case_insensitive": case_insensitive,
                "fixed_strings": fixed_strings,
                "context_before": context_before,
                "context_after": context_after,
                "max_results": max_results,
                "max_file_size": max_file_size,
                "multiline": multiline,
                "offset": offset,
                "type": file_type,
                "recursive": recursive,
            },
            FileGrepResult,
            provider_metadata,
        )

    async def glob_files(
        self,
        base_url: str,
        path: str,
        pattern: str,
        *,
        exclude: tuple[str, ...] | None = None,
        include_hidden: bool = False,
        files_only: bool = True,
        include_metadata: bool = True,
        max_results: int = 5000,
        sort_by: FileGlobSortField = FileGlobSortField.PATH,
        sort_desc: bool = False,
        provider_metadata: Mapping[str, object] | None = None,
    ) -> FileGlobResult:
        return await self._post_file_result(
            base_url,
            "/v1/file/glob",
            {
                "path": path,
                "pattern": pattern,
                "exclude": exclude,
                "include_hidden": include_hidden,
                "files_only": files_only,
                "include_metadata": include_metadata,
                "max_results": max_results,
                "sort_by": sort_by.value,
                "sort_desc": sort_desc,
            },
            FileGlobResult,
            provider_metadata,
        )

    async def list_files(
        self,
        base_url: str,
        path: str,
        *,
        recursive: bool = False,
        show_hidden: bool = True,
        file_types: tuple[str, ...] | None = None,
        max_depth: int | None = None,
        include_size: bool = True,
        include_permissions: bool = False,
        sort_by: FileListSortField = FileListSortField.NAME,
        sort_desc: bool = False,
        provider_metadata: Mapping[str, object] | None = None,
    ) -> FileListResult:
        return await self._post_file_result(
            base_url,
            "/v1/file/list",
            {
                "path": path,
                "recursive": recursive,
                "show_hidden": show_hidden,
                "file_types": file_types,
                "max_depth": max_depth,
                "include_size": include_size,
                "include_permissions": include_permissions,
                "sort_by": sort_by.value,
                "sort_desc": sort_desc,
            },
            FileListResult,
            provider_metadata,
        )

    async def upload_file(
        self,
        base_url: str,
        path: str,
        source: BinaryIO,
        filename: str,
        *,
        provider_metadata: Mapping[str, object] | None = None,
    ) -> FileUploadResult:
        source.seek(0)
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(self._file_transfer_timeout_seconds),
            trust_env=False,
            headers=self._headers(provider_metadata),
        ) as client:
            response = await client.post(
                f"{base_url.rstrip('/')}/v1/file/upload",
                data={"path": path},
                files={"file": (filename, source, "application/octet-stream")},
            )
        return self._parse_file_result(response, FileUploadResult)

    async def download_file(
        self,
        base_url: str,
        path: str,
        destination: BinaryIO,
        max_bytes: int,
        *,
        provider_metadata: Mapping[str, object] | None = None,
    ) -> FileDownloadResult:
        total = 0
        digest = hashlib.md5()
        timeout = httpx.Timeout(self._file_transfer_timeout_seconds)
        async with httpx.AsyncClient(
            timeout=timeout,
            trust_env=False,
            headers=self._headers(provider_metadata),
        ) as client:
            async with client.stream(
                "GET",
                f"{base_url.rstrip('/')}/v1/file/download",
                params={"path": path, "change_policy": "abort"},
            ) as response:
                response.raise_for_status()
                content_length = response.headers.get("content-length")
                try:
                    declared_size = int(content_length) if content_length else None
                except ValueError:
                    declared_size = None
                if declared_size is not None and declared_size > max_bytes:
                    raise FileTransferTooLargeError(max_bytes)
                async for chunk in response.aiter_bytes():
                    total += len(chunk)
                    if total > max_bytes:
                        raise FileTransferTooLargeError(max_bytes)
                    digest.update(chunk)
                    destination.write(chunk)
        destination.seek(0)
        return FileDownloadResult(size=total, md5=digest.hexdigest())

    async def _post_file_result(
        self,
        base_url: str,
        endpoint: str,
        request_data: Mapping[str, object | None],
        result_type: type[FileResultT],
        provider_metadata: Mapping[str, object] | None,
    ) -> FileResultT:
        async with httpx.AsyncClient(
            timeout=self._request_timeout_seconds,
            trust_env=False,
            headers=self._headers(provider_metadata),
        ) as client:
            response = await client.post(
                f"{base_url.rstrip('/')}{endpoint}",
                json={key: value for key, value in request_data.items() if value is not None},
            )
        return self._parse_file_result(response, result_type)

    @staticmethod
    def _parse_file_result(
        response: httpx.Response,
        result_type: type[FileResultT],
    ) -> FileResultT:
        response.raise_for_status()
        try:
            payload = response.json()
        except ValueError as exc:
            raise RuntimeError("AIO file API returned non-JSON data") from exc
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(payload, dict) or payload.get("success") is not True:
            message = payload.get("message") if isinstance(payload, dict) else None
            raise RuntimeError(f"AIO file operation failed: {message or 'invalid response'}")
        try:
            return result_type.model_validate(data)
        except ValidationError as exc:
            raise RuntimeError(
                f"AIO file API returned invalid {result_type.__name__} data"
            ) from exc
