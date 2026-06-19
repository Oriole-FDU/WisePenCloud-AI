from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from chat.application.tools.document_tools.document_parse.models import (
    DocumentParseRequest,
    ParserRole,
)
from chat.application.tools.document_tools.document_parse.parsers.docling import DoclingParser
from chat.application.tools.document_tools.document_parse.parsers.image_ocr import ImageOcrParser
from chat.application.tools.document_tools.document_parse.parsers.markitdown import MarkItDownParser
from chat.application.tools.document_tools.document_parse.parsers.pandas_spreadsheet import (
    PandasSpreadsheetParser,
)
from chat.application.tools.document_tools.document_parse.parsers.pdf_strategy import PdfParseStrategy
from chat.application.tools.document_tools.document_parse.parsers.protocols import Parser
from chat.application.tools.utils.file_type_detect import detect_file_type
from chat.application.tools.utils.markdown_renderer import TableMarkdownRenderer


@dataclass(frozen=True, slots=True)
class ParseCandidate:
    """解析候选者：封装了具体的解析内核及其在链路中所扮演的语义角色。"""
    parser: Parser
    role: ParserRole


@dataclass(frozen=True, slots=True)
class ParsePlan:
    """解析计划：按优先级从高到低排列的顺序责任链。"""
    candidates: tuple[ParseCandidate, ...]


class DocumentParsePlanner:
    """文档解析计划器，基于文件静态特征动态路由生成内核候选链。"""

    def __init__(
            self,
            *,
            ocr_client: Any | None = None,
            table_renderer: TableMarkdownRenderer | None = None,
    ) -> None:
        self._ocr_client = ocr_client
        self._table_renderer = table_renderer or TableMarkdownRenderer()

    def plan(self, request: DocumentParseRequest) -> ParsePlan:
        """根据输入请求的文件类型和 MIME 生成渐进式降级的解析链。"""
        detected_type = detect_file_type(request.file_path)
        mime_type = (request.mime_type or detected_type.mime_type).lower()
        label = detected_type.label

        candidates: list[ParseCandidate] = []

        # 1. 策略路由：PDF 涉及复杂的混合双层文本与图像 OCR，交由专职策略对象处理
        if label == "pdf" or mime_type == "application/pdf":
            candidates.append(
                ParseCandidate(
                    parser=PdfParseStrategy(ocr_client=self._ocr_client),
                    role=ParserRole.STRATEGY,
                )
            )

        # 2. 核心主干路由：常规富文本 Office 及超文本格式，分发给高精度版面分析内核
        elif label in {"docx", "pptx", "html"} or mime_type in {
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            "text/html",
            "application/xhtml+xml",
        }:
            candidates.append(ParseCandidate(parser=DoclingParser(), role=ParserRole.PRIMARY))

        # 3. 数据表格路由：专职电子表格流，交由 Pandas 解析器执行规整渲染
        elif label == "xlsx" or mime_type in {
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        }:
            candidates.append(
                ParseCandidate(
                    parser=PandasSpreadsheetParser(table_renderer=self._table_renderer),
                    role=ParserRole.PRIMARY,
                )
            )

        # 4. 纯视觉路由：图像类型直接派发给通用 OCR 引擎
        elif mime_type.startswith("image/"):
            candidates.append(
                ParseCandidate(
                    parser=ImageOcrParser(ocr_client=self._ocr_client),
                    role=ParserRole.OCR,
                )
            )

        # 5. 全能兜底：所有请求（包括未知或异构格式）最终均追加全局 Fallback 解析内核
        candidates.append(ParseCandidate(parser=MarkItDownParser(), role=ParserRole.FALLBACK))

        return ParsePlan(candidates=tuple(candidates))