import asyncio
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Sequence

import httpx

from chat.domain.interfaces import AttachmentParser, AttachmentParseResult
from common.clients.file_storage import FileStorageClient


class LegacyOfficeAttachmentParser(AttachmentParser):
    """旧版 Office 文档解析器"""

    _SUPPORTED_EXTENSIONS = {"doc", "ppt", "xls"}
    _SUMMARY_LIMIT = 120
    _EXCERPT_LIMIT = 300
    _DEFAULT_CONVERTER_COMMANDS = ("soffice", "soffice.exe", "libreoffice")

    def __init__(
        self,
        file_storage_client: FileStorageClient,
        converter_command: str = "soffice",
        converter_timeout_seconds: int = 120,
    ):
        self._file_storage_client = file_storage_client
        self._converter_command = converter_command.strip()
        self._converter_timeout_seconds = converter_timeout_seconds
        self._http = httpx.AsyncClient(timeout=httpx.Timeout(self._converter_timeout_seconds + 30))

    async def parse(
        self,
        object_key: str,
        filename: str,
        extension: str,
    ) -> AttachmentParseResult:
        if extension not in self._SUPPORTED_EXTENSIONS:
            raise ValueError(f"当前暂不支持旧版 Office 自动解析 {extension} 格式文件")

        download_url = await self._file_storage_client.get_download_url(object_key)
        resp = await self._http.get(download_url)
        resp.raise_for_status()

        with tempfile.TemporaryDirectory(prefix="legacy-office-parse-") as temp_dir:
            source_path = Path(temp_dir) / f"source.{extension}"
            output_dir = Path(temp_dir) / "output"
            await asyncio.to_thread(source_path.write_bytes, resp.content)
            pdf_path = await asyncio.to_thread(
                self._convert_to_pdf,
                source_path,
                output_dir,
            )
            text = await asyncio.to_thread(self._extract_pdf_text, pdf_path)

        text = self._normalize_text(text)
        if not text:
            raise ValueError("未解析出可用文本")

        return AttachmentParseResult(
            summary=text[:self._SUMMARY_LIMIT],
            content_excerpt=text[:self._EXCERPT_LIMIT],
            extracted_text=text,
        )

    def _convert_to_pdf(self, source_path: Path, output_dir: Path) -> Path:
        output_dir.mkdir(parents=True, exist_ok=True)
        expected_pdf = output_dir / f"{source_path.stem}.pdf"
        errors = []

        for command in self._get_converter_commands():
            try:
                subprocess.run(
                    [
                        command,
                        "--headless",
                        "--convert-to",
                        "pdf",
                        "--outdir",
                        str(output_dir),
                        str(source_path),
                    ],
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=self._converter_timeout_seconds,
                )
            except FileNotFoundError as exc:
                errors.append(str(exc))
                continue
            except subprocess.TimeoutExpired as exc:
                raise ValueError("旧版 Office 转 PDF 超时，请检查 LibreOffice 服务状态") from exc
            except subprocess.CalledProcessError as exc:
                detail = (exc.stderr or exc.stdout or "").strip()
                raise ValueError(f"旧版 Office 转 PDF 失败: {detail or '未知错误'}") from exc

            if expected_pdf.exists():
                return expected_pdf
            raise ValueError("旧版 Office 转 PDF 失败: 未生成目标 PDF 文件")

        raise ValueError(
            "旧版 Office 自动解析依赖 LibreOffice，请先安装并确保 `soffice` 可执行。"
            + (f" 最近错误: {errors[-1]}" if errors else "")
        )

    def _get_converter_commands(self) -> Sequence[str]:
        if self._converter_command:
            return (self._converter_command,)
        return self._DEFAULT_CONVERTER_COMMANDS

    @staticmethod
    def _extract_pdf_text(pdf_path: Path) -> str:
        try:
            from pypdf import PdfReader  # type: ignore
        except Exception as exc:
            raise ValueError("PDF 自动解析依赖 pypdf，当前环境未安装") from exc

        reader = PdfReader(str(pdf_path))
        texts = []
        for page in reader.pages:
            texts.append(page.extract_text() or "")
        return "\n".join(texts)

    @staticmethod
    def _normalize_text(text: str) -> str:
        text = text.replace("\x00", " ")
        text = re.sub(r"\s+", " ", text)
        return text.strip()
