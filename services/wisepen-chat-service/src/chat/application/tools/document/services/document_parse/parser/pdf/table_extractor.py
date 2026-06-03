import contextlib
import sys
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import cv2

from chat.application.tools.document.services.document_parse.enums import ParserName
from chat.application.tools.document.services.document_parse.errors import (
    DocumentParseError,
)
from chat.application.tools.document.services.document_parse.models import ParsedTable
from chat.application.tools.document.services.document_parse.utils.text import (
    normalize_text,
)

_SCANNED_TABLE_BACKEND = ParserName.PP_STRUCTURE
_NATIVE_TABLE_BACKEND = ParserName.PYMUPDF


def _format_table_rows(rows: List[List[Any]]) -> str:
    """将表格行列表渲染为 Markdown 格式的表格字符串。"""
    normalized_rows = _normalize_table_rows(rows)
    if not normalized_rows:
        return ""

    table_rows = _rectangularize_table_rows(normalized_rows)
    if not table_rows:
        return ""

    header = table_rows[0]
    separator = ["---"] * len(header)

    lines = [
        _format_markdown_table_row(header),
        _format_markdown_table_row(separator),
    ]
    lines.extend(_format_markdown_table_row(row) for row in table_rows[1:])

    return "\n".join(lines)


def _normalize_table_rows(rows: List[List[Any]]) -> List[List[str]]:
    """归一化表格行：清洗单元格值，过滤空行。"""
    normalized_rows: List[List[str]] = []

    for row in rows or []:
        if row is None:
            continue

        cells = row if isinstance(row, list) else [row]
        normalized_row = [_normalize_table_cell(cell) for cell in cells]

        if any(cell for cell in normalized_row):
            normalized_rows.append(normalized_row)

    return normalized_rows


def _normalize_table_cell(cell: Any) -> str:
    """归一化表格单元格值，列表嵌套递归清洗。"""
    if cell is None:
        return ""

    if isinstance(cell, list):
        return " ".join(
            part for part in (_normalize_table_cell(value) for value in cell) if part
        )

    return " ".join(normalize_text(str(cell)).split())


def _rectangularize_table_rows(rows: List[List[str]]) -> List[List[str]]:
    """将不规则表格补齐为矩形矩阵，移除全空列。"""
    max_columns = max((len(row) for row in rows), default=0)
    if max_columns <= 0:
        return []

    padded_rows = [row + [""] * (max_columns - len(row)) for row in rows]
    keep_columns = [
        index for index in range(max_columns) if any(row[index] for row in padded_rows)
    ]

    if not keep_columns:
        return []

    return [[row[index] for index in keep_columns] for row in padded_rows]


def _format_markdown_table_row(row: List[str]) -> str:
    """将一行单元格格式化为 Markdown 表格行（ | 分隔）。"""
    return "| " + " | ".join(_escape_markdown_table_cell(cell) for cell in row) + " |"


def _escape_markdown_table_cell(cell: str) -> str:
    """转义 Markdown 表格单元格中的特殊字符（反斜杠与竖线）。"""
    return cell.replace("\\", "\\\\").replace("|", "\\|")


class TableExtractor:
    """
    扫描页表格抽取器，使用 PPStructure 识别扫描图片中的表格结构。

    输入为 PDF 页面渲染后的 PNG 图片路径，输出为 Markdown 格式的表格文本和 ParsedTable 对象。
    """

    def __init__(self, *, pp_structure_engine: Any) -> None:
        """初始化 TableExtractor，注入 PPStructure 引擎实例。"""
        self.pp_structure_engine = pp_structure_engine

    def extract(
        self,
        *,
        image_path: Path,
        page_index: int,
    ) -> Tuple[str, List[ParsedTable]]:
        """使用 PPStructure 从渲染图片中识别表格，返回 Markdown 文本和 ParsedTable 列表。"""
        image = cv2.imread(str(image_path))
        if image is None:
            raise DocumentParseError(f"Failed to read rendered page image: {image_path}")

        with self._redirect_stdout_to_stderr():
            results = self.pp_structure_engine(image)

        blocks: List[str] = []
        parsed_tables: List[ParsedTable] = []

        for table_index, item in enumerate(results or [], 1):
            rows = self._pp_structure_item_to_rows(item)
            table_text = _format_table_rows(rows)

            if not table_text:
                continue

            blocks.append(table_text)

            parsed_tables.append(
                ParsedTable(
                    table_id=f"pdf_page_{page_index}_scanned_table_{table_index}",
                    source=_SCANNED_TABLE_BACKEND,
                    rows=rows,
                    page_index=page_index,
                    metadata=self._pp_structure_metadata(item),
                )
            )

        if not blocks:
            return "", []

        return self._format_table_text_blocks(blocks), parsed_tables

    def extract_scanned_from_image(
        self,
        *,
        image_path: Path,
        page_index: int,
    ) -> Tuple[str, List[ParsedTable]]:
        """从扫描页渲染图片中抽取表格。"""
        return self.extract(image_path=image_path, page_index=page_index)

    def extract_native_from_page(
        self,
        *,
        page: Any,
        page_index: int,
        warnings: List[str],
    ) -> Tuple[str, List[ParsedTable]]:
        """从 PyMuPDF 页面抽取原生表格，失败时记录 warning 并返回空结果。"""
        try:
            tables = page.find_tables()
        except Exception as e:
            warnings.append(
                f"native_table_extract_failed: page={page_index + 1}: "
                f"{type(e).__name__}: {e}"
            )
            return "", []

        blocks: List[str] = []
        parsed_tables: List[ParsedTable] = []

        for table_index, table in enumerate(getattr(tables, "tables", []) or [], 1):
            rows = self._native_table_to_rows(table)
            table_text = _format_table_rows(rows)

            if not table_text:
                continue

            blocks.append(table_text)
            parsed_tables.append(
                ParsedTable(
                    table_id=f"pdf_page_{page_index}_native_table_{table_index}",
                    source=_NATIVE_TABLE_BACKEND,
                    rows=rows,
                    page_index=page_index,
                    metadata=self._native_table_metadata(table),
                )
            )

        if not blocks:
            return "", []

        return self._format_table_text_blocks(blocks), parsed_tables

    def _pp_structure_item_to_rows(self, item: Any) -> List[List[str]]:
        """
        PaddleOCR 2.10.0 PPStructure table item 协议：

        - item["type"] == "table"
        - item["res"]["html"] 是表格 HTML 字符串
        - item["res"]["cell_bbox"] 是单元格 bbox，本 extractor 暂不消费
        """
        if not isinstance(item, dict):
            raise DocumentParseError("Invalid PPStructure result item: not a dict.")

        if item.get("type") != "table":
            return []

        result = item.get("res")
        if not isinstance(result, dict):
            raise DocumentParseError("Invalid PPStructure table item: res is not a dict.")

        html = result.get("html")
        if not isinstance(html, str) or not html.strip():
            raise DocumentParseError("Invalid PPStructure table item: res.html is empty.")

        return self._html_table_to_rows(html)

    def _pp_structure_metadata(self, item: Any) -> Dict[str, Any]:
        """从 PPStructure 结果项提取元数据。"""
        if not isinstance(item, dict):
            return {}

        result = item.get("res")
        cell_bbox = result.get("cell_bbox") if isinstance(result, dict) else None

        return {
            "bbox": item.get("bbox"),
            "score": item.get("score"),
            "img_idx": item.get("img_idx"),
            "cell_bbox": cell_bbox,
        }

    def _native_table_to_rows(self, table: Any) -> List[List[str]]:
        rows = table.extract()
        return _normalize_table_rows(rows)

    def _native_table_metadata(self, table: Any) -> Dict[str, Any]:
        bbox = getattr(table, "bbox", None)
        row_count = getattr(table, "row_count", None)
        col_count = getattr(table, "col_count", None)

        return {
            "bbox": bbox,
            "row_count": row_count,
            "col_count": col_count,
        }

    def _html_table_to_rows(self, html: str) -> List[List[str]]:
        """使用 HTMLParser 解析表格 HTML，提取为行列表。"""
        class TableParser(HTMLParser):
            """HTML 表格解析器，将 <table> 元素转为行列表。"""
            def __init__(self) -> None:
                """初始化解析器状态：行、单元格、colspan/rowspan 追踪。"""
                super().__init__()
                self.rows: List[List[str]] = []
                self._current_row: Optional[List[str]] = None
                self._current_cell: Optional[List[str]] = None
                self._current_colspan = 1
                self._current_rowspan = 1
                self._in_cell = False
                self._rowspans: Dict[int, Tuple[int, str]] = {}

            def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
                """处理开始标签，识别 tr/td/th 并初始化单元格状态。"""
                if tag == "tr":
                    self._current_row = []
                    return

                if tag not in {"td", "th"} or self._current_row is None:
                    return

                self._apply_pending_rowspans()
                self._current_cell = []
                attr_map = dict(attrs or [])
                self._current_colspan = self._parse_span(attr_map.get("colspan"))
                self._current_rowspan = self._parse_span(attr_map.get("rowspan"))
                self._in_cell = True

            def handle_data(self, data: str) -> None:
                """收集单元格内的文本数据。"""
                if not self._in_cell or self._current_cell is None:
                    return

                text = data.strip()
                if text:
                    self._current_cell.append(text)

            def handle_endtag(self, tag: str) -> None:
                """处理结束标签，完成单元格/行处理，支持 colspan/rowspan。"""
                if tag in {"td", "th"} and self._current_row is not None:
                    cell_text = normalize_text(" ".join(self._current_cell or []))
                    start_col = len(self._current_row)

                    self._current_row.append(cell_text)
                    for _ in range(1, self._current_colspan):
                        self._current_row.append("")

                    if self._current_rowspan > 1:
                        for col_offset in range(self._current_colspan):
                            self._rowspans[start_col + col_offset] = (
                                self._current_rowspan - 1,
                                cell_text if col_offset == 0 else "",
                            )

                    self._current_cell = None
                    self._current_colspan = 1
                    self._current_rowspan = 1
                    self._in_cell = False
                    return

                if tag == "tr" and self._current_row is not None:
                    self._apply_pending_rowspans()
                    if any(cell for cell in self._current_row):
                        self.rows.append(self._current_row)
                    self._current_row = None

            def _apply_pending_rowspans(self) -> None:
                """将待处理的 rowspan 单元格填充到当前行。"""
                if self._current_row is None:
                    return

                while len(self._current_row) in self._rowspans:
                    column = len(self._current_row)
                    remaining, text = self._rowspans[column]
                    self._current_row.append(text)

                    if remaining <= 1:
                        del self._rowspans[column]
                    else:
                        self._rowspans[column] = (remaining - 1, text)

            def _parse_span(self, value: Optional[str]) -> int:
                """解析 HTML colspan/rowspan 属性值，非法值返回 1。"""
                if value is None:
                    return 1

                try:
                    span = int(value)
                except ValueError:
                    return 1

                return max(1, span)

        parser = TableParser()
        parser.feed(html)
        return _normalize_table_rows(parser.rows)

    def _format_table_text_blocks(self, blocks: List[str]) -> str:
        """将多个表格 Markdown 文本块拼接为完整输出，每个表格前加三级标题。"""
        content: List[str] = []

        for index, block in enumerate(blocks, 1):
            content.append(f"### Table {index}")
            content.append("")
            content.append(block)
            content.append("")

        return normalize_text("\n".join(content))

    @contextlib.contextmanager
    def _redirect_stdout_to_stderr(self):
        """将 stdout 重定向到 stderr，抑制 PPStructure 的无关打印输出。"""
        original_stdout = sys.stdout
        sys.stdout = sys.stderr

        try:
            yield
        finally:
            sys.stdout = original_stdout
