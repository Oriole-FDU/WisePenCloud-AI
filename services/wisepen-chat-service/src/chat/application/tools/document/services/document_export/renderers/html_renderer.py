import asyncio
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import List, Optional, Tuple

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

DEFAULT_DOCUMENT_CSS = """
body {
  color: #202124;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI",
    "Microsoft YaHei", sans-serif;
  font-size: 14px;
  line-height: 1.6;
  margin: 32px auto;
  max-width: 860px;
  padding: 0 24px;
}
pre {
  background: #f6f8fa;
  border: 1px solid #d0d7de;
  border-radius: 6px;
  padding: 12px;
  white-space: pre-wrap;
  word-break: break-word;
}
table {
  border-collapse: collapse;
  width: 100%;
}
th, td {
  border: 1px solid #d0d7de;
  padding: 6px 8px;
  text-align: left;
}
blockquote {
  border-left: 4px solid #d0d7de;
  color: #4b5563;
  padding-left: 12px;
}
img {
  max-width: 100%;
  height: auto;
}
@page {
  margin: 18mm 16mm;
}
@media print {
  html {
    -webkit-print-color-adjust: exact;
    print-color-adjust: exact;
  }
  body {
    color: #111827;
    font-size: 12pt;
    margin: 0;
    max-width: none;
    padding: 0;
  }
  main {
    width: 100%;
  }
  h1, h2, h3, h4 {
    break-after: avoid;
    page-break-after: avoid;
  }
  pre, blockquote, table, img {
    break-inside: avoid;
    page-break-inside: avoid;
  }
  pre {
    overflow-wrap: anywhere;
    white-space: pre-wrap;
  }
  table {
    display: table;
    table-layout: auto;
  }
  thead {
    display: table-header-group;
  }
  tr {
    break-inside: avoid;
    page-break-inside: avoid;
  }
  img {
    max-width: 100%;
  }
  a {
    color: inherit;
    text-decoration: underline;
  }
}
""".strip()


class HtmlRenderer(DocumentRenderer):
    """
    Markdown -> HTML 渲染器。

    - 使用 Pandoc 将 canonical Markdown 转成完整 HTML。
    - CSS 同时服务 HTML 预览和 PDF 打印链路。
    - assets_dir 会加入 Pandoc resource-path，供图片等资源解析。
    """

    def __init__(
        self,
        *,
        pandoc_bin: str = "pandoc",
        css: str = DEFAULT_DOCUMENT_CSS,
    ) -> None:
        """初始化 HtmlRenderer，设置 Pandoc 路径与 CSS 样式。"""
        self.pandoc_bin = pandoc_bin
        self.css = css

    @property
    def target_format(self) -> ExportFormat:
        """返回目标格式 `ExportFormat.HTML`。"""
        return ExportFormat.HTML

    async def render(self, request: ExportRequest) -> None:
        """将 Markdown 渲染为 HTML 文件。"""
        html = await self.render_to_string(
            request.markdown,
            title=request.options.title,
            css=request.options.css,
            assets_dir=request.options.assets_dir,
            timeout_seconds=request.options.timeout_seconds,
        )

        await asyncio.to_thread(
            request.output_path.write_text,
            html,
            encoding="utf-8",
            newline="\n",
        )

    async def render_to_string(
        self,
        markdown: str,
        *,
        title: Optional[str] = None,
        css: Optional[str] = None,
        assets_dir: Optional[Path] = None,
        timeout_seconds: float = 60.0,
    ) -> str:
        """将 Markdown 渲染为完整 HTML 字符串（含 Pandoc 转换 + CSS 注入）。"""
        with TemporaryDirectory(prefix="document-export-html-") as tmp_dir:
            input_path = Path(tmp_dir) / "input.md"

            # Pandoc 从临时 Markdown 文件读取输入，和 DOCX renderer 保持一致。
            await asyncio.to_thread(
                input_path.write_text,
                markdown,
                encoding="utf-8",
                newline="\n",
            )

            args = self._build_pandoc_args(
                input_path=input_path,
                output_path=None,
                title=title,
                assets_dir=assets_dir,
            )

            stdout = await self._run_pandoc_with_compat_fallback(
                args=args,
                cwd=input_path.parent,
                timeout_seconds=timeout_seconds,
            )

            html = stdout.decode("utf-8", errors="replace")
            return self._inject_css(html=html, css=css or self.css)

    async def _run_pandoc_with_compat_fallback(
        self,
        *,
        args: List[str],
        cwd: Path,
        timeout_seconds: float,
    ) -> bytes:
        stdout, stderr, returncode = await self._run_pandoc(
            args=args,
            cwd=cwd,
            timeout_seconds=timeout_seconds,
        )
        if returncode == 0:
            return stdout

        message = self._format_stderr(stderr)
        if self._should_retry_with_self_contained(message):
            fallback_args = [
                "--self-contained" if arg == "--embed-resources" else arg
                for arg in args
            ]
            stdout, stderr, returncode = await self._run_pandoc(
                args=fallback_args,
                cwd=cwd,
                timeout_seconds=timeout_seconds,
            )
            if returncode == 0:
                return stdout
            message = self._format_stderr(stderr)

        raise ExportRenderError(
            "HtmlRenderer failed: Pandoc exited with code "
            f"{returncode}. {message}"
        )

    async def _run_pandoc(
        self,
        *,
        args: List[str],
        cwd: Path,
        timeout_seconds: float,
    ) -> Tuple[bytes, bytes, int]:
        process = await asyncio.create_subprocess_exec(
            *args,
            cwd=str(cwd),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=timeout_seconds,
            )
        except asyncio.TimeoutError as e:
            process.kill()
            _, stderr = await process.communicate()
            message = self._format_stderr(stderr)
            raise ExportTimeoutError(
                f"HtmlRenderer timed out: {message}"
            ) from e

        return stdout, stderr, process.returncode

    def _build_pandoc_args(
        self,
        *,
        input_path: Path,
        output_path: Optional[Path],
        title: Optional[str],
        assets_dir: Optional[Path],
    ) -> List[str]:
        """构建 Pandoc 命令行参数：GFM → HTML5，嵌入资源并设置资源路径。"""
        args = [
            self.pandoc_bin,
            str(input_path),
            "--from=gfm",
            "--to=html5",
            "--standalone",
            "--embed-resources",
            "--highlight-style=pygments",
        ]

        if title:
            args.extend(["--metadata", f"title={title}"])

        if output_path is not None:
            args.extend(["--output", str(output_path)])

        resource_paths: List[str] = []
        for path in (input_path.parent, assets_dir):
            if path is None:
                continue

            value = str(path)
            if value not in resource_paths:
                resource_paths.append(value)

        args.extend(["--resource-path", os.pathsep.join(resource_paths)])
        return args

    @staticmethod
    def _should_retry_with_self_contained(message: str) -> bool:
        lower_message = message.lower()
        return (
            "embed-resources" in lower_message
            and (
                "unknown option" in lower_message
                or "unrecognized option" in lower_message
                or "option" in lower_message
            )
        )

    def _inject_css(self, *, html: str, css: str) -> str:
        """将 CSS 注入到 HTML 的 <head> 中。"""
        style = f"<style>\n{css}\n</style>\n"

        if "</head>" in html:
            return html.replace("</head>", f"{style}</head>", 1)

        return (
            "<!doctype html>\n"
            "<html>\n"
            "<head>\n"
            '<meta charset="utf-8">\n'
            f"{style}"
            "</head>\n"
            "<body>\n"
            f"{html}\n"
            "</body>\n"
            "</html>\n"
        )

    @staticmethod
    def _format_stderr(stderr: bytes) -> str:
        """格式化 Pandoc 错误输出，超出长度限制时截断。"""
        message = stderr.decode("utf-8", errors="replace").strip()
        if not message:
            return "No stderr output."
        if len(message) <= _PANDOC_STDERR_LIMIT:
            return message
        return message[:_PANDOC_STDERR_LIMIT] + "..."
