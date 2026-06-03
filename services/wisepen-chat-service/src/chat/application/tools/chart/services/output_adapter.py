import asyncio
from pathlib import Path
from typing import Callable
from urllib.parse import quote
from uuid import uuid4

from chat.application.tools.chart.services.models import GeneratedChartFile
from chat.application.tools.document.services.document_export.runtime.atomic_writer import (
    AtomicExportWriter,
)
from chat.application.tools.document.services.document_export.utils.path import (
    display_file_name,
    document_export_output_path,
    is_path_within_root,
    sanitize_path_segment,
    storage_stem_for_download_ref,
)
from chat.application.tools.document.services.document_export.errors import ExportOutputError


_DOWNLOAD_PATH = "/api/document-export/download"
_CONTENT_TYPES = {
    "png": "image/png",
    "svg": "image/svg+xml",
}


class ChartTempOutputAdapter:
    """图表临时文件输出适配器。

    - 复用 document_export 的统一临时根目录、路径清洗与下载引用格式。
    - renderer 只拿到系统分配的临时 output_path。
    - image_file_ref 当前等同 download_ref，正式前端可通过下载接口预览。
    """

    def __init__(self, *, output_root: Path, atomic_writer: AtomicExportWriter) -> None:
        """初始化适配器。

        Args:
            output_root: 仓库统一导出临时文件根目录。
            atomic_writer: document_export 已有原子写入器。
        """
        self.output_root = output_root
        self.atomic_writer = atomic_writer

    async def write_chart(
        self,
        *,
        user_id: str,
        session_id: str,
        title: str,
        output_format: str,
        render: Callable[[Path], None],
    ) -> GeneratedChartFile:
        """写入图表文件并返回统一文件引用。

        Args:
            user_id: 当前用户 ID。
            session_id: 当前会话 ID。
            title: 图表标题，用于生成可读文件名。
            output_format: png 或 svg。
            render: 同步 renderer 回调，只写入传入路径。
        """
        suffix = f".{output_format}"
        safe_user = sanitize_path_segment(user_id, fallback="user")
        safe_session = sanitize_path_segment(session_id, fallback="default")
        safe_stem = sanitize_path_segment(title, fallback="chart")
        safe_stem = storage_stem_for_download_ref(
            safe_stem=safe_stem,
            suffix=suffix,
        )
        output_path = (
            self.output_root
            / safe_user
            / safe_session
            / "outputs"
            / f"{uuid4().hex}-{safe_stem}{suffix}"
        )

        if not is_path_within_root(output_path, self.output_root):
            raise ExportOutputError("Resolved chart output path escapes output root.")

        async def render_to_tmp(tmp_path: Path) -> None:
            await asyncio.to_thread(render, tmp_path)

        await self.atomic_writer.write_with_renderer(
            output_path=output_path,
            render=render_to_tmp,
        )

        size_bytes = await asyncio.to_thread(lambda: output_path.stat().st_size)
        download_ref = f"{safe_user}/{safe_session}/{output_path.name}"
        preview_url = f"{_DOWNLOAD_PATH}?ref={quote(download_ref, safe='')}"
        return GeneratedChartFile(
            file_path=output_path,
            storage_file_name=output_path.name,
            file_name=display_file_name(storage_file_name=output_path.name),
            user_id=user_id,
            session_id=session_id,
            content_type=_CONTENT_TYPES[output_format],
            output_format=output_format,
            size_bytes=size_bytes,
            image_file_ref=download_ref,
            preview_url=preview_url,
        )


def build_default_chart_output_adapter() -> ChartTempOutputAdapter:
    """构建默认图表输出适配器。"""
    return ChartTempOutputAdapter(
        output_root=document_export_output_path(),
        atomic_writer=AtomicExportWriter(),
    )
