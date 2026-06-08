import re
from pathlib import Path
from typing import Any, Dict, List, Tuple

import pandas as pd

from chat.application.tools.document.services.document_parse.enums import (
    DocumentType,
    PageType,
    ParserName,
)
from chat.application.tools.document.services.document_parse.models import (
    DocumentParseResult,
    ParsedPage,
    ParsedTable,
)
from chat.application.tools.document.services.document_parse.parsers.base import (
    BaseDocumentParser,
)
from chat.application.tools.document.services.document_parse.utils.text import normalize_text


def _format_cell(value: object) -> str:
    """清洗并格式化单元格文本，将换行替换为斜杠，压缩多余空格。"""
    if value is None:
        return ""

    text = str(value)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n+", " / ", text)
    return text.strip()


class SpreadsheetParser(BaseDocumentParser):
    """电子表格文档解析器，使用 Pandas 读取并转为 Markdown/TSV 文本。"""

    @property
    def name(self) -> ParserName:
        """返回解析器名称 `SpreadsheetParser`。"""
        return ParserName.SPREADSHEET

    @property
    def supported_extensions(self) -> Tuple[str, ...]:
        """支持 Excel 和 ODS 电子表格格式：.xlsx, .xls, .xlsm, .ods。"""
        return (".xlsx", ".xls", ".xlsm", ".ods")

    async def parse(self, path: Path) -> DocumentParseResult:
        """使用 pandas 读取 Excel 的所有 sheet，转换为 Markdown 嵌入的 TSV 表格文本。"""
        sheets = pd.read_excel(
            path, sheet_name=None, dtype=object, keep_default_na=False
        )

        text_parts: List[str] = []
        tables: List[ParsedTable] = []
        sheets_info: List[Dict[str, Any]] = []

        for sheet_name, df in sheets.items():
            sheet_name_text = str(sheet_name)
            sheet_number = len(tables) + 1

            # 内联原 _rows_from_dataframe 逻辑：提取表头和行数据并清洗
            filled = df.fillna("")
            rows: List[List[str]] = [[_format_cell(col) for col in filled.columns.tolist()]]
            rows.extend(
                [[_format_cell(value) for value in row] for row in filled.values.tolist()]
            )

            # 封装当前 sheet 的结构化表格对象
            table = ParsedTable(
                table_id=f"sheet_{sheet_number}",
                source=ParserName.PANDAS,
                rows=rows,
                page_index=None,
                metadata={
                    "sheet_name": sheet_name_text,
                    "rows": len(df),
                    "columns": len(df.columns),
                },
            )
            tables.append(table)

            # 收集元数据信息
            sheets_info.append(
                {
                    "name": sheet_name_text,
                    "rows": len(df),
                    "columns": len(df.columns),
                }
            )

            # 将当前 sheet 转换为 Markdown 标题和 tsv 代码块文本
            text_parts.append(f"## Sheet: {sheet_name_text}")
            text_parts.append("")
            text_parts.append("```tsv")
            text_parts.extend(["\t".join(row) for row in rows])
            text_parts.append("```")
            text_parts.append("")

        # 合并所有文本并进行归一化处理
        text = normalize_text("\n".join(text_parts))

        # 构建单页解析对象
        page = ParsedPage(
            page_index=0,
            text=text,
            page_type=PageType.SPREADSHEET,
            tables=tables,
            metadata={"parsers": ParserName.PANDAS},
        )

        # 组装最终的文档解析结果
        result = DocumentParseResult(
            text=text,
            source=str(path),
            file_type=DocumentType.SPREADSHEET,
            pages=[page],
            tables=tables,
            metadata={
                "parsers": self.name,
                "selected_parser": ParserName.PANDAS,
                "spreadsheet_backend": ParserName.PANDAS,
                "sheet_count": len(sheets),
                "sheets": sheets_info,
                "page_count": 1,
                "table_count": len(tables),
            },
            warnings=[],
        )

        return result
