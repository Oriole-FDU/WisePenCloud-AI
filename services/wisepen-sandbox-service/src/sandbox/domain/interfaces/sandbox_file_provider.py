from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from enum import StrEnum
from typing import BinaryIO

from pydantic import BaseModel, ConfigDict, Field


class FileContentEncoding(StrEnum):
    UTF8 = "utf-8"
    BASE64 = "base64"
    RAW = "raw"


class FileGlobSortField(StrEnum):
    PATH = "path"
    NAME = "name"
    SIZE = "size"
    MODIFIED = "modified"


class FileListSortField(StrEnum):
    NAME = "name"
    SIZE = "size"
    MODIFIED = "modified"
    TYPE = "type"


class FileTransferTooLargeError(Exception):
    def __init__(self, max_bytes: int) -> None:
        self.max_bytes = max_bytes
        super().__init__(f"file exceeds transfer limit of {max_bytes} bytes")

class FileReadResult(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    path: str = Field(alias="file")
    content: str


class FileWriteResult(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    path: str = Field(alias="file")
    bytes_written: int | None = None


class FileReplaceResult(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    path: str = Field(alias="file")
    replaced_count: int = 0


class FileSearchResult(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    path: str = Field(alias="file")
    matches: list[str] = Field(default_factory=list)
    line_numbers: list[int] = Field(default_factory=list)


class FileFindResult(BaseModel):
    path: str
    files: list[str] = Field(default_factory=list)


class FileGrepMatch(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    path: str = Field(alias="file")
    line_number: int
    line_content: str
    context_before: list[str] | None = None
    context_after: list[str] | None = None


class FileGrepResult(BaseModel):
    path: str
    pattern: str
    matches: list[FileGrepMatch] = Field(default_factory=list)
    match_count: int = 0
    files_searched: int | None = None
    files_matched: int | None = None
    truncated: bool = False


class FileGlobEntry(BaseModel):
    path: str
    name: str
    is_directory: bool = False
    size: int | None = None
    modified_time: str | None = None


class FileGlobResult(BaseModel):
    path: str
    pattern: str
    files: list[FileGlobEntry] = Field(default_factory=list)
    total_count: int = 0
    truncated: bool = False


class FileEntry(BaseModel):
    name: str
    path: str
    is_directory: bool
    size: int | None = None
    modified_time: str | None = None
    permissions: str | None = None
    extension: str | None = None


class FileListResult(BaseModel):
    path: str
    files: list[FileEntry] = Field(default_factory=list)
    total_count: int = 0
    directory_count: int = 0
    file_count: int = 0


class FileUploadResult(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    path: str = Field(alias="file_path")
    size: int = Field(alias="file_size")


class FileDownloadResult(BaseModel):
    size: int = Field(ge=0)
    md5: str


class SandboxFileProvider(ABC):
    """Provider-independent, explicitly typed sandbox file capability."""

    @abstractmethod
    async def read_file(
        self,
        base_url: str,
        path: str,
        *,
        start_line: int | None = None,
        end_line: int | None = None,
        provider_metadata: Mapping[str, object] | None = None,
    ) -> FileReadResult:
        pass

    @abstractmethod
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
        pass

    @abstractmethod
    async def replace_in_file(
        self,
        base_url: str,
        path: str,
        old_str: str,
        new_str: str,
        *,
        provider_metadata: Mapping[str, object] | None = None,
    ) -> FileReplaceResult:
        pass

    @abstractmethod
    async def search_in_file(
        self,
        base_url: str,
        path: str,
        regex: str,
        *,
        provider_metadata: Mapping[str, object] | None = None,
    ) -> FileSearchResult:
        pass

    @abstractmethod
    async def find_files(
        self,
        base_url: str,
        path: str,
        glob: str,
        *,
        provider_metadata: Mapping[str, object] | None = None,
    ) -> FileFindResult:
        pass

    @abstractmethod
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
        pass

    @abstractmethod
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
        pass

    @abstractmethod
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
        pass

    @abstractmethod
    async def upload_file(
        self,
        base_url: str,
        path: str,
        source: BinaryIO,
        filename: str,
        *,
        provider_metadata: Mapping[str, object] | None = None,
    ) -> FileUploadResult:
        pass

    @abstractmethod
    async def download_file(
        self,
        base_url: str,
        path: str,
        destination: BinaryIO,
        max_bytes: int,
        *,
        provider_metadata: Mapping[str, object] | None = None,
    ) -> FileDownloadResult:
        pass
