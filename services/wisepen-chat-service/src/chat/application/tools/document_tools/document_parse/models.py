from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path


class DocumentParseMonitorName(StrEnum):
    """解析链路监控名称，值保持短字符串，便于日志聚合。"""

    DOCLING = "docling.parse"
    FALLBACK = "fallback.markitdown"
    IMAGE_OCR = "image.ocr"
    OCR_PADDLE = "ocr.paddle.request"
    PDF = "pdf.strategy"
    SPREADSHEET = "spreadsheet.parse"


class ParserRole(StrEnum):
    """候选解析器在 ParsePlan 中承担的角色。"""

    PRIMARY = "primary"
    OCR = "ocr"
    STRATEGY = "strategy"
    FALLBACK = "fallback"


@dataclass(frozen=True, slots=True)
class DocumentParseRequest:
    file_path: str | Path  # 本地待解析文件路径
    original_filename: str | None = None  # 用户上传时的原始文件名
    mime_type: str | None = None  # 上游已知 MIME，缺失时由本地探测兜底

    @property
    def path(self) -> Path:
        return Path(self.file_path)

    @property
    def display_name(self) -> str:
        return self.original_filename or self.path.name

    @property
    def suffix(self) -> str:
        return self.path.suffix.lower()


@dataclass(frozen=True, slots=True)
class OcrPageResult:
    page_number: int  # 从 1 开始的页码
    markdown: str  # OCR 产出的页面 Markdown

    @property
    def page_marker(self) -> str:
        return f"<!-- page {self.page_number} -->"

    def markdown_with_page_marker(self) -> str:
        body = self.markdown.strip()
        return self.page_marker if not body else f"{self.page_marker}\n\n{body}"


@dataclass(frozen=True, slots=True)
class DocumentParseResult:
    markdown: str  # 最终提供给后续工具链的 Markdown
