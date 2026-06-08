from __future__ import annotations

from contextlib import nullcontext
from dataclasses import dataclass
from pathlib import Path
from typing import ContextManager, Optional

from chat.application.infra.document_temp_files.errors import (
    InvalidDocumentRefError as TempInvalidDocumentRefError,
    UnreadableDocumentRefError as TempUnreadableDocumentRefError,
)
from chat.application.infra.document_temp_files.processing_scope import document_processing_scope
from chat.application.infra.document_temp_files.resolver import DocumentTempFileResolver
from chat.application.tools.document.services.document_export.enums import ExportFormat, ExportSourceFormat
from chat.application.tools.document.services.document_export.errors import DocumentExportError
from chat.application.tools.document.services.document_export.models import GeneratedDocumentFile
from chat.application.tools.document.services.document_export.runtime.download_resolver import (
    DocumentDownloadResolver,
)
from chat.application.tools.document.services.document_export.runtime.models import ExportOptions
from chat.application.tools.document.services.document_export.service import DocumentExportService
from common.logger import log_event
from .converter import MarkdownConverter
from .errors import (
    DocumentConvertError,
    DocumentExportFailedError,
    EmptyParsedMarkdownError,
    InvalidDocumentRefError,
    UnreadableDocumentRefError,
)


@dataclass(frozen=True, slots=True)
class _ConversionSource:
    path: Path
    source_file_name: str
    source_kind: str


class DocumentConvertService:
    """
    文档转换服务。

    - 解析 file_ref 到真实临时文件。
    - 使用 MarkdownConverter 将源文件统一转换为 Markdown。
    - 使用 DocumentExportService 将 Markdown 导出为目标格式。
    """

    def __init__(
        self,
        *,
        markdown_converter: MarkdownConverter,
        export_service: DocumentExportService,
        temp_file_resolver: DocumentTempFileResolver,
        download_resolver: Optional[DocumentDownloadResolver] = None,
    ) -> None:
        """初始化对象依赖。"""
        self.markdown_converter = markdown_converter
        self.export_service = export_service
        self.temp_file_resolver = temp_file_resolver
        self.download_resolver = download_resolver

    async def convert_document(
        self,
        *,
        session_id: str,
        user_id: str,
        file_ref: str,
        target_format: ExportFormat,
        file_name: Optional[str] = None,
        title: Optional[str] = None,
        reference_docx_file_ref: Optional[str] = None,
    ) -> GeneratedDocumentFile:
        """转换当前流程。"""
        source = self._resolve_source(
            file_ref=file_ref,
            user_id=user_id,
            session_id=session_id,
        )

        reference_docx_path = self._resolve_reference_docx_path(
            reference_docx_file_ref=reference_docx_file_ref,
            user_id=user_id,
            session_id=session_id,
        )

        # 防止正在转换的上传暂存文件被定时清理脚本误删。
        with self._processing_scope_for_source(
            source=source,
            user_id=user_id,
            session_id=session_id,
        ):
            markdown = await self.markdown_converter.convert(path=source.path)
            if not markdown.strip():
                raise EmptyParsedMarkdownError()

            try:
                generated = await self.export_service.export_document(
                    user_id=user_id,
                    session_id=session_id,
                    content=markdown,
                    source_format=ExportSourceFormat.MARKDOWN,
                    target_format=target_format,
                    file_name=file_name,
                    options=ExportOptions(
                        title=title,
                        reference_docx=reference_docx_path,
                    ),
                )
            except DocumentExportError as e:
                raise DocumentExportFailedError(str(e)) from e

            # 只保留最终成功审计。
            log_event(
                "document_convert completed",
                user_id=user_id,
                session_id=session_id,
                source_file_ref=file_ref,
                source_path=str(source.path),
                source_kind=source.source_kind,
                target_format=target_format.value,
                output_file_name=file_name,
                storage_file_name=generated.storage_file_name,
                size_bytes=generated.size_bytes,
                reference_docx_used=reference_docx_path is not None,
            )

            return generated

    def _processing_scope_for_source(
        self,
        *,
        source: _ConversionSource,
        user_id: str,
        session_id: str,
    ) -> ContextManager[None]:
        """仅上传暂存文件需要 processing marker。"""
        if source.source_kind != "file_ref":
            return nullcontext()

        return document_processing_scope(
            self.temp_file_resolver.session_root(
                user_id=user_id,
                session_id=session_id,
            )
        )

    def _resolve_source(
        self,
        *,
        file_ref: str,
        user_id: str,
        session_id: str,
    ) -> _ConversionSource:
        """解析转换源，兼容上传 file_ref 与导出 download_ref。"""
        if self._looks_like_download_ref(file_ref):
            if self.download_resolver is None:
                raise InvalidDocumentRefError()

            try:
                resolved = self.download_resolver.resolve(
                    download_ref=file_ref,
                    user_id=user_id,
                )
            except DocumentExportError as e:
                raise InvalidDocumentRefError() from e

            path = resolved.file_path
            if not path.exists() or not path.is_file():
                raise UnreadableDocumentRefError()

            return _ConversionSource(
                path=path,
                source_file_name=resolved.file_name,
                source_kind="download_ref",
            )

        try:
            resolved_source = self.temp_file_resolver.resolve(
                file_ref=file_ref,
                user_id=user_id,
                session_id=session_id,
            )
        except TempInvalidDocumentRefError as e:
            raise InvalidDocumentRefError() from e
        except TempUnreadableDocumentRefError as e:
            raise UnreadableDocumentRefError() from e

        return _ConversionSource(
            path=resolved_source.path,
            source_file_name=resolved_source.source_file_name,
            source_kind="file_ref",
        )

    def _looks_like_download_ref(self, value: str) -> bool:
        """download_ref 固定为 user/session/storage_file_name 三段。"""
        return len(value.split("/")) == 3

    def _resolve_reference_docx_path(
        self,
        *,
        reference_docx_file_ref: Optional[str],
        user_id: str,
        session_id: str,
    ) -> Optional[Path]:
        """解析当前流程。"""
        if reference_docx_file_ref is None:
            return None

        source = self._resolve_source(
            file_ref=reference_docx_file_ref,
            user_id=user_id,
            session_id=session_id,
        )

        path = source.path
        if not path.exists() or not path.is_file():
            raise UnreadableDocumentRefError()

        if path.suffix.lower() != ".docx":
            raise DocumentConvertError(
                "reference_docx_file_ref must point to a .docx file"
            )

        return path
