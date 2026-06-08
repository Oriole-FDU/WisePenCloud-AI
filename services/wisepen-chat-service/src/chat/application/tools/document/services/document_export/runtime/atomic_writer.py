import asyncio
from pathlib import Path
from uuid import uuid4

from chat.application.tools.document.services.document_export.errors import ExportOutputError


class AtomicExportWriter:
    """原子写入器：先将内容写入临时文件，校验通过后替换到最终路径，失败时清理临时文件。"""
    async def write_with_renderer(self, *, output_path: Path, render) -> None:
        """通过渲染器生成文件，原子写入目标路径，确保不暴露半成品。"""
        await asyncio.to_thread(output_path.parent.mkdir, parents=True, exist_ok=True)

        # 临时文件与最终文件放在同一目录，保证替换尽量具备原子语义。
        tmp_path = output_path.with_name(f".{output_path.name}.{uuid4().hex}.tmp")

        try:
            # 渲染器只写临时文件，不直接触碰最终输出路径。
            await render(tmp_path)

            # 渲染器是外部传入能力，需要确认它确实产出了文件。
            is_file = await asyncio.to_thread(tmp_path.is_file)
            if not is_file:
                raise ExportOutputError("Export failed: output file was not created.")

            # 防止空文件被当作成功导出结果。
            size = await asyncio.to_thread(lambda: tmp_path.stat().st_size)
            if size <= 0:
                raise ExportOutputError("Export failed: output file is empty.")

            # 校验通过后再一次性替换最终文件，避免暴露半成品。
            await asyncio.to_thread(tmp_path.replace, output_path)

        finally:
            # 成功替换后临时路径通常已不存在；失败时清理残留临时文件。
            await asyncio.to_thread(tmp_path.unlink, missing_ok=True)
