from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, BinaryIO

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
    FileUploadResult,
    FileWriteResult,
    ProviderCommandHandle,
    ProviderCommandSnapshot,
    SandboxProvider,
    SandboxProvision,
    SandboxProviderType,
)


class SandboxProviderManager:
    """按 provider_id 管理并显式路由沙箱生命周期、命令和文件能力"""

    def __init__(
        self,
        provider_classes: Sequence[type[SandboxProvider]],
        provider_settings: dict[SandboxProviderType, dict[str, Any]],
    ) -> None:
        self._providers: dict[SandboxProviderType, SandboxProvider] = {}
        for provider_class in provider_classes:
            provider_id = provider_class.sandbox_provider_id()
            config = provider_settings.get(provider_id)
            if config is not None:
                self._providers[provider_id] = provider_class.from_config(config)

    def create_provision(self, provider_id: str | SandboxProviderType) -> SandboxProvision:
        return self._get(provider_id).create_provision()

    def _get(self, provider_id: str | SandboxProviderType) -> SandboxProvider:
        provider = self._providers.get(SandboxProviderType(provider_id))
        if provider is None:
            raise ValueError(f"sandbox provider is not configured: {provider_id}")
        return provider

    async def check_ready(
        self,
        provider_id: str,
        base_url: str | None,
        provider_metadata: Mapping[str, Any] | None = None,
    ) -> bool:
        provider = self._providers.get(SandboxProviderType(provider_id))
        return False if provider is None else await provider.check_ready(base_url, provider_metadata)

    async def start_command(
        self,
        provider_id: str,
        base_url: str,
        command: str,
        working_directory: str,
        timeout_seconds: int,
        provider_metadata: Mapping[str, Any] | None = None,
    ) -> ProviderCommandHandle:
        return await self._get(provider_id).start_command(
            base_url, command, working_directory, timeout_seconds, provider_metadata,
        )

    async def get_command_result(
        self,
        provider_id: str,
        base_url: str,
        handle: ProviderCommandHandle,
        stdout_offset: int,
        stderr_offset: int,
        wait_timeout_seconds: float,
        provider_metadata: Mapping[str, Any] | None = None,
    ) -> ProviderCommandSnapshot:
        return await self._get(provider_id).get_command_result(
            base_url,
            handle,
            stdout_offset,
            stderr_offset,
            wait_timeout_seconds,
            provider_metadata,
        )

    async def kill_command(
        self,
        provider_id: str,
        base_url: str,
        handle: ProviderCommandHandle,
        provider_metadata: Mapping[str, Any] | None = None,
    ) -> None:
        await self._get(provider_id).kill_command(base_url, handle, provider_metadata)

    async def read_file(
        self,
        provider_id: str,
        base_url: str,
        path: str,
        *,
        start_line: int | None = None,
        end_line: int | None = None,
        provider_metadata: Mapping[str, object] | None = None,
    ) -> FileReadResult:
        return await self._get(provider_id).read_file(
            base_url,
            path,
            start_line=start_line,
            end_line=end_line,
            provider_metadata=provider_metadata,
        )

    async def write_file(
        self,
        provider_id: str,
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
        return await self._get(provider_id).write_file(
            base_url,
            path,
            content,
            encoding=encoding,
            append=append,
            leading_newline=leading_newline,
            trailing_newline=trailing_newline,
            provider_metadata=provider_metadata,
        )

    async def replace_in_file(
        self,
        provider_id: str,
        base_url: str,
        path: str,
        old_str: str,
        new_str: str,
        *,
        provider_metadata: Mapping[str, object] | None = None,
    ) -> FileReplaceResult:
        return await self._get(provider_id).replace_in_file(
            base_url,
            path,
            old_str,
            new_str,
            provider_metadata=provider_metadata,
        )

    async def search_in_file(
        self,
        provider_id: str,
        base_url: str,
        path: str,
        regex: str,
        *,
        provider_metadata: Mapping[str, object] | None = None,
    ) -> FileSearchResult:
        return await self._get(provider_id).search_in_file(
            base_url, path, regex, provider_metadata=provider_metadata,
        )

    async def find_files(
        self,
        provider_id: str,
        base_url: str,
        path: str,
        glob: str,
        *,
        provider_metadata: Mapping[str, object] | None = None,
    ) -> FileFindResult:
        return await self._get(provider_id).find_files(
            base_url, path, glob, provider_metadata=provider_metadata,
        )

    async def grep_files(
        self,
        provider_id: str,
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
        return await self._get(provider_id).grep_files(
            base_url,
            path,
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
            provider_metadata=provider_metadata,
        )

    async def glob_files(
        self,
        provider_id: str,
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
        return await self._get(provider_id).glob_files(
            base_url,
            path,
            pattern,
            exclude=exclude,
            include_hidden=include_hidden,
            files_only=files_only,
            include_metadata=include_metadata,
            max_results=max_results,
            sort_by=sort_by,
            sort_desc=sort_desc,
            provider_metadata=provider_metadata,
        )

    async def list_files(
        self,
        provider_id: str,
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
        return await self._get(provider_id).list_files(
            base_url,
            path,
            recursive=recursive,
            show_hidden=show_hidden,
            file_types=file_types,
            max_depth=max_depth,
            include_size=include_size,
            include_permissions=include_permissions,
            sort_by=sort_by,
            sort_desc=sort_desc,
            provider_metadata=provider_metadata,
        )

    async def upload_file(
        self,
        provider_id: str,
        base_url: str,
        path: str,
        source: BinaryIO,
        filename: str,
        *,
        provider_metadata: Mapping[str, object] | None = None,
    ) -> FileUploadResult:
        return await self._get(provider_id).upload_file(
            base_url,
            path,
            source,
            filename,
            provider_metadata=provider_metadata,
        )

    async def download_file(
        self,
        provider_id: str,
        base_url: str,
        path: str,
        destination: BinaryIO,
        max_bytes: int,
        *,
        provider_metadata: Mapping[str, object] | None = None,
    ) -> FileDownloadResult:
        return await self._get(provider_id).download_file(
            base_url,
            path,
            destination,
            max_bytes,
            provider_metadata=provider_metadata,
        )
