from __future__ import annotations

from pathlib import Path

import pandas as pd

from chat.application.tools.document_tools.document_parse.errors import PrimaryParserError
from chat.application.tools.document_tools.document_parse.models import (
    DocumentParseMonitorName,
    DocumentParseRequest,
    DocumentParseResult,
)
from chat.application.tools.utils.markdown_renderer import TableMarkdownRenderer


class PandasSpreadsheetParser:
    """基于 pandas/openpyxl 的 xlsx 解析器。"""

    def __init__(self, *, table_renderer: TableMarkdownRenderer | None = None) -> None:
        self._table_renderer = table_renderer or TableMarkdownRenderer()

    async def parse(self, request: DocumentParseRequest) -> DocumentParseResult:
        try:
            file_path = Path(request.file_path)
            sheet_frames = pd.read_excel(
                file_path,
                sheet_name=None,
                header=None,
                dtype=object,
                engine="openpyxl",
            )

            sections: list[str] = []
            for sheet_name, frame in sheet_frames.items():
                # 每个 sheet 单独渲染，避免跨表混淆结构。
                values = frame.where(pd.notna(frame), None).values.tolist()
                render_result = self._table_renderer.render(values)
                sections.append(f"## {sheet_name}\n\n{render_result.markdown}".strip())

            return DocumentParseResult(
                markdown="\n\n".join(sections).strip(),
            )
        except Exception as e:
            raise PrimaryParserError(
                "Spreadsheet parser failed.",
                parser_name=DocumentParseMonitorName.SPREADSHEET,
                cause=e,
            ) from e
