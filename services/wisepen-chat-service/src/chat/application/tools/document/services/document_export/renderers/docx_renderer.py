import asyncio
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import List

from chat.application.tools.document.services.document_export.enums import ExportFormat
from chat.application.tools.document.services.document_export.errors import (
    ExportRenderError,
    ExportTimeoutError,
)
from chat.application.tools.document.services.document_export.models import ExportRequest
from chat.application.tools.document.services.document_export.renderers.base import (
    DocumentRenderer,
)

_PANDOC_STDERR_LIMIT = 1000


class DocxRenderer(DocumentRenderer):
    """
    Markdown -> DOCX 渲染器。

    - 使用 Pandoc 将临时 Markdown 文件转换为 DOCX。
    - 支持 reference_docx 模板。
    - assets_dir 会加入 Pandoc resource-path，供图片等资源解析。
    """

    def __init__(self, *, pandoc_bin: str = "pandoc") -> None:
        """初始化 DocxRenderer，设置 Pandoc 路径。"""
        self.pandoc_bin = pandoc_bin

    @property
    def target_format(self) -> ExportFormat:
        """返回目标格式 `ExportFormat.DOCX`。"""
        return ExportFormat.DOCX

    async def render(self, request: ExportRequest) -> None:
        """将 Markdown 渲染为 DOCX 文件。"""
        reference_docx = request.options.reference_docx
        if reference_docx is not None and not reference_docx.is_file():
            raise ExportRenderError(
                "DocxRenderer failed for target "
                f"{request.output_path}: reference_docx is not a file: "
                f"{reference_docx}"
            )

        with TemporaryDirectory(prefix="document-export-docx-") as tmp_dir:
            input_path = Path(tmp_dir) / "input.md"

            # Pandoc 从临时 Markdown 文件读取输入，避免把大文本塞进 stdin。
            await asyncio.to_thread(
                input_path.write_text,
                request.markdown,
                encoding="utf-8",
                newline="\n",
            )

            args = self._build_pandoc_args(
                input_path=input_path,
                request=request,
            )

            process = await asyncio.create_subprocess_exec(
                *args,
                cwd=str(input_path.parent),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            try:
                _, stderr = await asyncio.wait_for(
                    process.communicate(),
                    timeout=request.options.timeout_seconds,
                )
            except asyncio.TimeoutError as e:
                # 超时后杀掉 Pandoc 子进程，避免残留后台进程。
                process.kill()
                _, stderr = await process.communicate()
                message = self._format_stderr(stderr)
                raise ExportTimeoutError(
                    "DocxRenderer timed out for target "
                    f"{request.output_path}: {message}"
                ) from e

            if process.returncode != 0:
                message = self._format_stderr(stderr)
                raise ExportRenderError(
                    "DocxRenderer failed for target "
                    f"{request.output_path}: Pandoc exited with code "
                    f"{process.returncode}. {message}"
                )

    def _build_pandoc_args(
        self,
        *,
        input_path: Path,
        request: ExportRequest,
    ) -> List[str]:
        # 基础转换：GFM Markdown -> DOCX。
        """构建 Pandoc 命令行参数：GFM → DOCX，支持 reference_docx 模板和资源路径。"""
        args = [
            self.pandoc_bin,
            str(input_path),
            "--from=gfm",
            "--to=docx",
            "--output",
            str(request.output_path),
        ]

        # reference_docx 用于控制 Word 样式模板。
        if request.options.reference_docx is not None:
            args.extend(["--reference-doc", str(request.options.reference_docx)])

        # resource-path 用于解析 Markdown 中的相对资源路径，例如图片。
        resource_paths: List[str] = []
        for path in (input_path.parent, request.options.assets_dir):
            if path is None:
                continue

            value = str(path)
            if value not in resource_paths:
                resource_paths.append(value)

        args.extend(["--resource-path", os.pathsep.join(resource_paths)])
        return args

    @staticmethod
    def _format_stderr(stderr: bytes) -> str:
        """格式化 Pandoc 错误输出，超出长度限制时截断。"""
        message = stderr.decode("utf-8", errors="replace").strip()
        if not message:
            return "No stderr output."
        if len(message) <= _PANDOC_STDERR_LIMIT:
            return message
        return message[:_PANDOC_STDERR_LIMIT] + "..."
