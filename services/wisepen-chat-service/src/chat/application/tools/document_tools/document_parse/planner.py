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
    parser: Parser  # 实际执行解析的能力对象或策略对象
    role: ParserRole  # 候选在当前计划中的职责


@dataclass(frozen=True, slots=True)
class ParsePlan:
    candidates: tuple[ParseCandidate, ...]  # 按执行优先级排列的候选链


class DocumentParsePlanner:
    """根据文件特征生成解析候选链。

    Planner 只决定“尝试哪些能力以及顺序”，不执行解析，也不处理异常。
    通用 parser（Docling、MarkItDown）在这里被赋予具体角色，避免 parser
    自身承担过宽的语义。
    """

    def __init__(
        self,
        *,
        ocr_client: Any | None = None,
        table_renderer: TableMarkdownRenderer | None = None,
    ) -> None:
        self._ocr_client = ocr_client
        self._table_renderer = table_renderer or TableMarkdownRenderer()

    def plan(self, request: DocumentParseRequest) -> ParsePlan:
        """生成解析计划。

        Args:
            request: 文档解析请求，包含文件路径和可选 MIME。

        Returns:
            按优先级排列的解析候选计划。最后总会追加 MarkItDown 兜底候选。
        """
        detected_type = detect_file_type(request.file_path)
        mime_type = (request.mime_type or detected_type.mime_type).lower()
        label = detected_type.label
        candidates: list[ParseCandidate] = []

        # PDF 需要按页混合文本抽取和 OCR，因此作为策略候选，而不是普通格式 parser。
        if label == "pdf" or mime_type == "application/pdf":
            candidates.append(
                ParseCandidate(
                    parser=PdfParseStrategy(ocr_client=self._ocr_client),
                    role=ParserRole.STRATEGY,
                )
            )
        # Docling 覆盖多个文档格式，格式语义由计划层赋予。
        elif label in {"docx", "pptx", "html"} or mime_type in {
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            "text/html",
            "application/xhtml+xml",
        }:
            candidates.append(ParseCandidate(parser=DoclingParser(), role=ParserRole.PRIMARY))
        elif label == "xlsx" or mime_type in {
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        }:
            candidates.append(
                ParseCandidate(
                    parser=PandasSpreadsheetParser(table_renderer=self._table_renderer),
                    role=ParserRole.PRIMARY,
                )
            )
        elif mime_type.startswith("image/"):
            candidates.append(
                ParseCandidate(
                    parser=ImageOcrParser(ocr_client=self._ocr_client),
                    role=ParserRole.OCR,
                )
            )

        # 未知类型也允许进入 MarkItDown；失败由 Service 统一汇总。
        candidates.append(ParseCandidate(parser=MarkItDownParser(), role=ParserRole.FALLBACK))
        return ParsePlan(candidates=tuple(candidates))
