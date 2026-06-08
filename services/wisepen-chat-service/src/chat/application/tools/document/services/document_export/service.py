import asyncio
from pathlib import Path
from typing import Dict, Optional
from uuid import uuid4

from chat.application.tools.document.services.document_export.enums import (
    ExportFormat,
    ExportSourceFormat,
)
from chat.application.tools.document.services.document_export.errors import ExportOutputError
from chat.application.tools.document.services.document_export.models import (
    ExportOptions,
    ExportRequest,
    GeneratedDocumentFile,
)
from chat.application.tools.document.services.document_export.renderers.base import (
    DocumentRenderer,
)
from chat.application.tools.document.services.document_export.renderers.markdown_renderer import (
    MarkdownRenderer,
)
from chat.application.tools.document.services.document_export.runtime.atomic_writer import (
    AtomicExportWriter,
)
from chat.application.tools.document.services.document_export.utils.path import (
    display_file_name,
    is_path_within_root,
    sanitize_path_segment,
    storage_stem_for_download_ref,
)
from common.logger import log_event


class DocumentExportService:
    """
    文档导出服务门面。

    - 输入是业务 content，来源可以是 markdown 或 plain_text。
    - MarkdownRenderer.render_to_markdown() 负责生成 canonical Markdown。
    - 后续所有 renderer 统一只依赖 canonical Markdown。
    - AtomicExportWriter 负责原子写入最终文件。
    """

    def __init__(
        self,
        *,
        output_root: Path,
        markdown_renderer: MarkdownRenderer,
        renderers: Dict[ExportFormat, DocumentRenderer],
        atomic_writer: AtomicExportWriter,
    ) -> None:
        """初始化 DocumentExportService，注入 Markdown 中间渲染器、目标格式渲染器及原子写入器。"""
        self.output_root = output_root
        self.markdown_renderer = markdown_renderer
        self.renderers = renderers
        self.atomic_writer = atomic_writer

    async def export_document(
        self,
        *,
        user_id: str,
        session_id: str,
        content: str,
        source_format: ExportSourceFormat,
        target_format: ExportFormat,
        file_name: Optional[str] = None,
        options: Optional[ExportOptions] = None,
    ) -> GeneratedDocumentFile:
        """执行文档导出：内容转 canonical Markdown → 渲染目标格式 → 原子写入 → 返回元数据。"""
        renderer = self.renderers.get(target_format)
        if renderer is None:
            raise ExportOutputError(
                f"No document renderer registered for format: {target_format.value}"
            )

        # Markdown 中间层：
        # - source_format=markdown：保留 Markdown 结构。
        # - source_format=plain_text：转成保真的 canonical Markdown。
        canonical_markdown = self.markdown_renderer.render_to_markdown(
            content=content,
            source_format=source_format,
        )

        # 输出路径按 user/session 隔离，并生成内部存储文件名。
        output_path = self._build_new_export_storage_path(
            user_id=user_id,
            session_id=session_id,
            export_format=target_format,
            file_name=file_name,
        )

        # resolve 后确认最终路径没有逃逸 output_root。
        if not is_path_within_root(output_path, self.output_root):
            raise ExportOutputError("Resolved output path escapes output root.")

        export_options = options or ExportOptions()

        async def render_to_tmp(tmp_path: Path) -> None:
            """调用目标 renderer 将内容渲染到临时文件路径。"""
            request = ExportRequest(
                user_id=user_id,
                session_id=session_id,
                markdown=canonical_markdown,
                target_format=target_format,
                output_path=tmp_path,
                file_name=file_name,
                options=export_options,
            )
            await renderer.render(request)

        # 原子写入：先写临时文件，校验成功后 replace 到 output_path。
        await self.atomic_writer.write_with_renderer(
            output_path=output_path,
            render=render_to_tmp,
        )

        # 写入成功后读取最终文件大小，并返回下载所需元数据。
        size_bytes = await asyncio.to_thread(lambda: output_path.stat().st_size)

        result = GeneratedDocumentFile(
            file_path=output_path,
            file_name=display_file_name(storage_file_name=output_path.name),
            storage_file_name=output_path.name,
            user_id=user_id,
            session_id=session_id,
            content_type=target_format.content_type,
            target_format=target_format,
            size_bytes=size_bytes,
        )

        log_event(
            "document_export completed",
            user_id=user_id,
            session_id=session_id,
            source_format=source_format.value,
            target_format=target_format.value,
            storage_file_name=result.storage_file_name,
            size_bytes=result.size_bytes,
        )

        return result

    def _build_new_export_storage_path(
        self,
        *,
        user_id: str,
        session_id: str,
        export_format: ExportFormat,
        file_name: Optional[str],
    ) -> Path:
        """构建导出文件存储路径，按 user/session 隔离，文件名含 uuid 防冲突。"""
        suffix = export_format.extension
        safe_user = sanitize_path_segment(user_id, fallback="user")
        safe_session = sanitize_path_segment(session_id, fallback="default")

        # 用户文件名只取 stem；存储名额外加 uuid 前缀防冲突。
        if file_name:
            safe_stem = sanitize_path_segment(
                Path(file_name).stem or "document",
                fallback="document",
            )
        else:
            safe_stem = f"document-{uuid4().hex}"

        safe_stem = storage_stem_for_download_ref(
            safe_stem=safe_stem,
            suffix=suffix,
        )

        # 目录结构：
        # output_root / user / session / outputs / <uuid>-<safe_stem><suffix>
        return (
            self.output_root
            / safe_user
            / safe_session
            / "outputs"
            / f"{uuid4().hex}-{safe_stem}{suffix}"
        )
